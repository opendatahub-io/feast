/*
Copyright 2024 Feast Community.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package services

import (
	"os"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// isDataRegistryEnabled returns true when the FeatureStore CR carries the
// catalog annotation set to "true". Follows the isProtectedProject() pattern.
func (feast *FeastServices) isDataRegistryEnabled() bool {
	annotations := feast.Handler.FeatureStore.GetAnnotations()
	return annotations[DataRegistryAnnotation] == "true"
}

// deployDataRegistry creates (or updates) the full data-registry resource set
// when the catalog annotation is present: a two-container Deployment
// (feast-server + kube-rbac-proxy), an HTTPS Service, an auth.yaml ConfigMap,
// and aggregated ClusterRoles.
// When the annotation is removed or absent, all resources are cleaned up.
func (feast *FeastServices) deployDataRegistry() error {
	if !feast.isDataRegistryEnabled() {
		return feast.cleanupDataRegistryResources()
	}

	if err := feast.deployDataRegistryAuthConfig(); err != nil {
		return err
	}
	if err := feast.deployDataRegistryClusterRoles(); err != nil {
		return err
	}
	if err := feast.createDataRegistryService(); err != nil {
		return err
	}
	if err := feast.createDataRegistryDeployment(); err != nil {
		return err
	}
	return nil
}

// cleanupDataRegistryResources removes all data-registry owned resources.
func (feast *FeastServices) cleanupDataRegistryResources() error {
	if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistryDeploy()); err != nil {
		return err
	}
	if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistrySvc()); err != nil {
		return err
	}
	if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistryAuthCM()); err != nil {
		return err
	}
	if err := feast.CleanupDataRegistryClusterRoles(); err != nil {
		return err
	}
	return nil
}

// ---------------------------------------------------------------------------
// Deployment
// ---------------------------------------------------------------------------

func (feast *FeastServices) createDataRegistryDeployment() error {
	logger := log.FromContext(feast.Handler.Context)
	deploy := feast.initDataRegistryDeploy()
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		deploy,
		controllerutil.MutateFn(func() error {
			return feast.setDataRegistryDeployment(deploy)
		}),
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "Deployment", deploy.Name, "operation", op)
	}
	return nil
}

func (feast *FeastServices) initDataRegistryDeploy() *appsv1.Deployment {
	deploy := &appsv1.Deployment{
		ObjectMeta: feast.GetObjectMetaType(DataRegistryFeastType),
	}
	deploy.SetGroupVersionKind(appsv1.SchemeGroupVersion.WithKind("Deployment"))
	return deploy
}

// setDataRegistryDeployment mutates deploy with the full two-container pod spec:
// feast-server (bound to localhost) + kube-rbac-proxy (exposed to the cluster).
func (feast *FeastServices) setDataRegistryDeployment(deploy *appsv1.Deployment) error {
	cr := feast.Handler.FeatureStore
	labels := feast.getFeastTypeLabels(DataRegistryFeastType)

	selectorLabels := map[string]string{
		NameLabelKey:        cr.Name,
		ServiceTypeLabelKey: string(DataRegistryFeastType),
	}

	var replicas int32 = 1
	deploy.Labels = labels
	deploy.Spec = appsv1.DeploymentSpec{
		Replicas: &replicas,
		Selector: metav1.SetAsLabelSelector(selectorLabels),
		Template: corev1.PodTemplateSpec{
			ObjectMeta: metav1.ObjectMeta{
				Labels: labels,
			},
			Spec: corev1.PodSpec{
				ServiceAccountName: feast.initFeastSA().Name,
				Volumes: []corev1.Volume{
					{
						Name: "auth-config",
						VolumeSource: corev1.VolumeSource{
							ConfigMap: &corev1.ConfigMapVolumeSource{
								LocalObjectReference: corev1.LocalObjectReference{
									Name: feast.dataRegistryAuthCMName(),
								},
							},
						},
					},
					{
						Name: "tls-certs",
						VolumeSource: corev1.VolumeSource{
							Secret: &corev1.SecretVolumeSource{
								SecretName: feast.dataRegistryTlsSecretName(),
							},
						},
					},
				},
			},
		},
	}

	feastCtr, err := feast.buildDataRegistryContainer()
	if err != nil {
		return err
	}
	proxyCtr := feast.buildKubeRBACProxyContainer()

	deploy.Spec.Template.Spec.Containers = []corev1.Container{feastCtr, proxyCtr}

	return controllerutil.SetControllerReference(cr, deploy, feast.Handler.Scheme)
}

// buildDataRegistryContainer returns the feast-server container spec.
// Binds strictly to loopback so all cluster traffic must go through the proxy.
// FEAST_PROJECT is set to empty string to enable multi-project dynamic routing.
func (feast *FeastServices) buildDataRegistryContainer() (corev1.Container, error) {
	image := getFeatureServerImage()

	fsYamlB64, err := feast.GetServiceFeatureStoreYamlBase64()
	if err != nil {
		return corev1.Container{}, err
	}

	probeHandler := corev1.ProbeHandler{
		HTTPGet: &corev1.HTTPGetAction{
			Path: "/v1/config",
			Port: intstr.FromInt32(DataRegistryPort),
			Host: DataRegistryLocalhostAddr,
		},
	}

	// SSAR env vars are consumed by the Feast Python server (not by kube-rbac-proxy).
	// The proxy uses auth.yaml for its initial authentication gate, but the server
	// performs its own SubjectAccessReview calls during cross-namespace search to
	// determine which namespaces a given user is authorized to browse.
	return corev1.Container{
		Name:  DataRegistryContainerName,
		Image: image,
		Command: []string{
			feastCommand,
			"serve_registry",
			"--rest-api",
			"-h", DataRegistryLocalhostAddr,
			"-p", "6572",
		},
		Env: []corev1.EnvVar{
			{Name: TmpFeatureStoreYamlEnvVar, Value: fsYamlB64},
			{Name: "FEAST_USAGE", Value: "False"},
			{Name: DataCatalogEnabledEnvVar, Value: "true"},
			{Name: CatalogSSARApiGroupEnvVar, Value: "dataregistry.opendatahub.io"},
			{Name: CatalogSSARResourcesEnvVar, Value: "namespaces,tables,volumes,generic-tables"},
			{Name: FeastProjectEnvVar, Value: ""},
		},
		ReadinessProbe: &corev1.Probe{
			ProbeHandler:  probeHandler,
			PeriodSeconds: 10,
		},
		LivenessProbe: &corev1.Probe{
			ProbeHandler:     probeHandler,
			PeriodSeconds:    20,
			FailureThreshold: 6,
		},
		StartupProbe: &corev1.Probe{
			ProbeHandler:     probeHandler,
			PeriodSeconds:    3,
			FailureThreshold: 40,
		},
	}, nil
}

// buildKubeRBACProxyContainer returns the kube-rbac-proxy sidecar spec.
// The proxy listens on 0.0.0.0:8443 (HTTPS) and forwards authenticated
// requests to the feast-server at 127.0.0.1:6572.
func (feast *FeastServices) buildKubeRBACProxyContainer() corev1.Container {
	return corev1.Container{
		Name:  DataRegistryProxyContainerName,
		Image: getKubeRBACProxyImage(),
		Args: []string{
			"--secure-listen-address=0.0.0.0:8443",
			"--upstream=http://127.0.0.1:6572/",
			"--config-file=/etc/kube-rbac-proxy/auth.yaml",
			"--tls-cert-file=/etc/tls/tls.crt",
			"--tls-private-key-file=/etc/tls/tls.key",
			"--logtostderr=true",
			"--v=3",
		},
		Ports: []corev1.ContainerPort{
			{
				Name:          "https",
				ContainerPort: DataRegistryProxyPort,
				Protocol:      corev1.ProtocolTCP,
			},
		},
		VolumeMounts: []corev1.VolumeMount{
			{
				Name:      "auth-config",
				MountPath: "/etc/kube-rbac-proxy",
				ReadOnly:  true,
			},
			{
				Name:      "tls-certs",
				MountPath: "/etc/tls",
				ReadOnly:  true,
			},
		},
		ReadinessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				TCPSocket: &corev1.TCPSocketAction{
					Port: intstr.FromInt32(DataRegistryProxyPort),
				},
			},
			PeriodSeconds: 10,
		},
		LivenessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				TCPSocket: &corev1.TCPSocketAction{
					Port: intstr.FromInt32(DataRegistryProxyPort),
				},
			},
			PeriodSeconds:    20,
			FailureThreshold: 6,
		},
	}
}

func getKubeRBACProxyImage() string {
	if img, exists := os.LookupEnv(kubeRBACProxyImageVar); exists {
		return img
	}
	return DefaultKubeRBACProxyImage
}

// ---------------------------------------------------------------------------
// Service — HTTPS endpoint exposed by kube-rbac-proxy
// ---------------------------------------------------------------------------

func (feast *FeastServices) createDataRegistryService() error {
	logger := log.FromContext(feast.Handler.Context)
	svc := feast.initDataRegistrySvc()
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		svc,
		controllerutil.MutateFn(func() error {
			return feast.setDataRegistryService(svc)
		}),
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "Service", svc.Name, "operation", op)
	}
	return nil
}

func (feast *FeastServices) initDataRegistrySvc() *corev1.Service {
	svc := &corev1.Service{
		ObjectMeta: feast.GetObjectMetaType(DataRegistryFeastType),
	}
	svc.SetGroupVersionKind(corev1.SchemeGroupVersion.WithKind("Service"))
	return svc
}

func (feast *FeastServices) setDataRegistryService(svc *corev1.Service) error {
	cr := feast.Handler.FeatureStore
	svc.Labels = feast.getFeastTypeLabels(DataRegistryFeastType)

	if svc.Annotations == nil {
		svc.Annotations = map[string]string{}
	}
	svc.Annotations[openshiftServingCertSecretAnnotation] = feast.dataRegistryTlsSecretName() // pragma: allowlist secret

	svc.Spec = corev1.ServiceSpec{
		Selector: map[string]string{
			NameLabelKey:        cr.Name,
			ServiceTypeLabelKey: string(DataRegistryFeastType),
		},
		Type: corev1.ServiceTypeClusterIP,
		Ports: []corev1.ServicePort{
			{
				Name:       HttpsScheme,
				Port:       int32(HttpsPort),
				Protocol:   corev1.ProtocolTCP,
				TargetPort: intstr.FromInt32(DataRegistryProxyPort),
			},
		},
	}

	return controllerutil.SetControllerReference(cr, svc, feast.Handler.Scheme)
}

// ---------------------------------------------------------------------------
// auth.yaml ConfigMap — kube-rbac-proxy SubjectAccessReview configuration
// ---------------------------------------------------------------------------

func (feast *FeastServices) deployDataRegistryAuthConfig() error {
	logger := log.FromContext(feast.Handler.Context)
	cm := feast.initDataRegistryAuthCM()
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		cm,
		controllerutil.MutateFn(func() error {
			return feast.setDataRegistryAuthConfig(cm)
		}),
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "ConfigMap", cm.Name, "operation", op)
	}
	return nil
}

func (feast *FeastServices) initDataRegistryAuthCM() *corev1.ConfigMap {
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      feast.dataRegistryAuthCMName(),
			Namespace: feast.Handler.FeatureStore.Namespace,
		},
	}
	cm.SetGroupVersionKind(corev1.SchemeGroupVersion.WithKind("ConfigMap"))
	return cm
}

func (feast *FeastServices) setDataRegistryAuthConfig(cm *corev1.ConfigMap) error {
	cr := feast.Handler.FeatureStore
	cm.Labels = feast.getFeastTypeLabels(DataRegistryFeastType)

	authYaml := `authorization:
  resourceAttributes:
    apiGroup: dataregistry.opendatahub.io
    resource: registries
`

	cm.Data = map[string]string{
		"auth.yaml": authYaml,
	}

	return controllerutil.SetControllerReference(cr, cm, feast.Handler.Scheme)
}

// ---------------------------------------------------------------------------
// Aggregated ClusterRoles — viewer and editor permissions for data registry
// ---------------------------------------------------------------------------

func (feast *FeastServices) deployDataRegistryClusterRoles() error {
	logger := log.FromContext(feast.Handler.Context)

	viewerCR := feast.initDataRegistryClusterRole("viewer")
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		viewerCR,
		func() error {
			viewerCR.Labels = map[string]string{
				NameLabelKey:      feast.Handler.FeatureStore.Name,
				ManagedByLabelKey: ManagedByLabelValue,
				"rbac.authorization.k8s.io/aggregate-to-view":           "true",
				"rbac.authorization.k8s.io/aggregate-to-edit":           "true",
				"rbac.authorization.k8s.io/aggregate-to-admin":          "true",
				"rbac.authorization.k8s.io/aggregate-to-cluster-reader": "true",
			}
			viewerCR.Rules = []rbacv1.PolicyRule{
				{
					APIGroups: []string{"dataregistry.opendatahub.io"},
					Resources: []string{"registries"},
					Verbs:     []string{"get", "list", "watch"},
				},
			}
			return nil
		},
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "ClusterRole", viewerCR.Name, "operation", op)
	}

	editorCR := feast.initDataRegistryClusterRole("editor")
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		editorCR,
		func() error {
			editorCR.Labels = map[string]string{
				NameLabelKey:      feast.Handler.FeatureStore.Name,
				ManagedByLabelKey: ManagedByLabelValue,
				"rbac.authorization.k8s.io/aggregate-to-edit":  "true",
				"rbac.authorization.k8s.io/aggregate-to-admin": "true",
			}
			editorCR.Rules = []rbacv1.PolicyRule{
				{
					APIGroups: []string{"dataregistry.opendatahub.io"},
					Resources: []string{"registries"},
					Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
				},
			}
			return nil
		},
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "ClusterRole", editorCR.Name, "operation", op)
	}

	return nil
}

func (feast *FeastServices) initDataRegistryClusterRole(suffix string) *rbacv1.ClusterRole {
	cr := &rbacv1.ClusterRole{
		ObjectMeta: metav1.ObjectMeta{
			Name: feast.dataRegistryClusterRoleName(suffix),
		},
	}
	cr.SetGroupVersionKind(rbacv1.SchemeGroupVersion.WithKind("ClusterRole"))
	return cr
}

// CleanupDataRegistryClusterRoles removes the aggregated ClusterRoles.
// ClusterRoles are cluster-scoped and cannot carry namespace-scoped owner
// references, so we delete by name + managed-by label check instead of
// using DeleteOwnedFeastObj. Exported for use by cleanupOnDeletion.
func (feast *FeastServices) CleanupDataRegistryClusterRoles() error {
	for _, suffix := range []string{"viewer", "editor"} {
		cr := &rbacv1.ClusterRole{}
		name := feast.dataRegistryClusterRoleName(suffix)
		if err := feast.Handler.Client.Get(
			feast.Handler.Context,
			types.NamespacedName{Name: name},
			cr,
		); err != nil {
			if apierrors.IsNotFound(err) {
				continue
			}
			return err
		}
		if cr.Labels[ManagedByLabelKey] == ManagedByLabelValue &&
			cr.Labels[NameLabelKey] == feast.Handler.FeatureStore.Name {
			if err := feast.Handler.Client.Delete(feast.Handler.Context, cr); err != nil && !apierrors.IsNotFound(err) {
				return err
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// Naming helpers
// ---------------------------------------------------------------------------

func (feast *FeastServices) dataRegistryAuthCMName() string {
	return GetFeastName(feast.Handler.FeatureStore) + dataRegistryAuthConfigSuffix
}

func (feast *FeastServices) dataRegistryTlsSecretName() string {
	return GetFeastName(feast.Handler.FeatureStore) + dataRegistryTlsSecretSuffix
}

func (feast *FeastServices) dataRegistryClusterRoleName(suffix string) string {
	return GetFeastName(feast.Handler.FeatureStore) + dataRegistryClusterRoleSuffix + "-" + suffix
}
