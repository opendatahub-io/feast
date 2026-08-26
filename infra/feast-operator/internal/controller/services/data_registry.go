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
	"fmt"
	"os"
	"strconv"
	"strings"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
	routev1 "github.com/openshift/api/route/v1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// isDataRegistryEnabled returns true when the FeatureStore CR carries the
// catalog annotation set to "true". Follows the isProtectedProject() pattern.
func (feast *FeastServices) isDataRegistryEnabled() bool {
	annotations := feast.Handler.FeatureStore.GetAnnotations()
	return annotations[DataRegistryAnnotation] == "true"
}

// validateDataRegistryAnnotation checks for common annotation misconfigurations
// (e.g. "True", "yes", "1") and logs a warning so operators can spot typos.
func (feast *FeastServices) validateDataRegistryAnnotation() {
	logger := log.FromContext(feast.Handler.Context)
	annotations := feast.Handler.FeatureStore.GetAnnotations()
	val, exists := annotations[DataRegistryAnnotation]
	if !exists {
		return
	}
	if val == "true" {
		return
	}
	lower := strings.ToLower(val)
	if lower == "true" || val == "yes" || val == "1" {
		logger.Info("Data registry annotation has a non-canonical value; only exact \"true\" enables it",
			"annotation", DataRegistryAnnotation, "value", val, "hint", "use \"true\" (lowercase)")
	}
}

// validateDataRegistrySingleton ensures only one FeatureStore CR cluster-wide
// has the data-registry annotation enabled. Returns an error if another CR
// already has it, preventing race conditions on the shared PostgreSQL backend
// and duplicate cluster-scoped resources.
func (feast *FeastServices) validateDataRegistrySingleton() error {
	var list feastdevv1.FeatureStoreList
	if err := feast.Handler.Client.List(feast.Handler.Context, &list); err != nil {
		return fmt.Errorf("failed to list FeatureStore CRs for singleton check: %w", err)
	}
	self := feast.Handler.FeatureStore
	for i := range list.Items {
		item := &list.Items[i]
		if item.Name == self.Name && item.Namespace == self.Namespace {
			continue
		}
		if item.DeletionTimestamp != nil {
			continue
		}
		if item.Annotations[DataRegistryAnnotation] == "true" {
			return fmt.Errorf(
				"data registry is already enabled on FeatureStore %s/%s; only one data-registry instance is allowed cluster-wide",
				item.Namespace, item.Name,
			)
		}
	}
	return nil
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
	if err := feast.deployDataRegistryAuthDelegatorBinding(); err != nil {
		return err
	}
	if err := feast.createDataRegistryService(); err != nil {
		return err
	}
	if err := feast.createDataRegistryDeployment(); err != nil {
		return err
	}
	if err := feast.deployDataRegistryCaBundleConfigMap(); err != nil {
		return err
	}
	if err := feast.createDataRegistryRoute(); err != nil {
		return err
	}
	return nil
}

// cleanupDataRegistryResources removes all data-registry owned resources.
func (feast *FeastServices) cleanupDataRegistryResources() error {
	if isOpenShift {
		if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistryRoute()); err != nil {
			return err
		}
	}
	if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistryDeploy()); err != nil {
		return err
	}
	if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistrySvc()); err != nil {
		return err
	}
	if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistryAuthCM()); err != nil {
		return err
	}
	if err := feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistryCaBundleCM()); err != nil {
		return err
	}
	if err := feast.CleanupDataRegistryClusterRoles(); err != nil {
		return err
	}
	if err := feast.CleanupDataRegistryAuthDelegatorBinding(); err != nil {
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
				Annotations: map[string]string{
					// kube-rbac-proxy reads auth.yaml only at process start.
					// Bump this when SAR config semantics change so pods roll.
					"dataregistry.opendatahub.io/auth-config-revision": "static-sar-v1",
				},
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
	// Generated feature_store.yaml points the file registry at /feast-data/registry.db.
	feast.mountEmptyDirVolumes(&deploy.Spec.Template.Spec)

	return controllerutil.SetControllerReference(cr, deploy, feast.Handler.Scheme)
}

// buildDataRegistryContainer returns the feast-server container spec.
// feast serve_registry (feature-server 0.66.x) has no --host/-h flag and binds
// uvicorn to 0.0.0.0. Do not pass a host flag; kube-rbac-proxy remains the
// cluster-facing listener. FEAST_PROJECT is empty for multi-project routing.
func (feast *FeastServices) buildDataRegistryContainer() (corev1.Container, error) {
	image := getFeatureServerImage()

	fsYamlB64, err := feast.GetServiceFeatureStoreYamlBase64()
	if err != nil {
		return corev1.Container{}, err
	}

	// TCP probe: REST /v1/config does not exist, and kubernetes auth (the
	// operator default) would 401 any HTTP path without a token. Kubelet
	// treats 401/404 as probe failure, so the pod never becomes Ready.
	probeHandler := corev1.ProbeHandler{
		TCPSocket: &corev1.TCPSocketAction{
			Port: intstr.FromInt32(DataRegistryPort),
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
			"--no-grpc",
			"--rest-api",
			"--rest-port", strconv.Itoa(int(DataRegistryPort)),
		},
		Env: []corev1.EnvVar{
			// Python create_feature_store() reads FEATURE_STORE_YAML_BASE64 and
			// materializes feature_store.yaml itself. TMP_ is only consumed by
			// the standard feast-init container, which this pod does not run.
			{Name: FeatureStoreYamlEnvVar, Value: fsYamlB64},
			{Name: "FEAST_USAGE", Value: "False"},
			{Name: DataCatalogEnabledEnvVar, Value: "true"},
			{Name: CatalogSSARApiGroupEnvVar, Value: dataRegistryAPIGroup},
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
			fmt.Sprintf("--secure-listen-address=0.0.0.0:%d", DataRegistryProxyPort),
			fmt.Sprintf("--upstream=http://%s:%d/", DataRegistryLocalhostAddr, DataRegistryPort),
			"--config-file=/etc/kube-rbac-proxy/auth.yaml",
			"--tls-cert-file=/etc/tls/tls.crt",
			"--tls-private-key-file=/etc/tls/tls.key",
			// /projects and /search bypass proxy auth because they require
			// server-side per-namespace SSAR filtering that cannot be expressed as a
			// single resource SAR. The Feast server reads the bearer token from the
			// request, performs TokenReview + per-namespace SubjectAccessReview, and
			// returns only authorized results.
			"--ignore-paths=/projects,/search,/api/v1/projects,/api/v1/search",
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
		Resources: corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse(DefaultKubeRBACProxyCPURequest),
				corev1.ResourceMemory: resource.MustParse(DefaultKubeRBACProxyMemoryRequest),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse(DefaultKubeRBACProxyCPULimit),
				corev1.ResourceMemory: resource.MustParse(DefaultKubeRBACProxyMemoryLimit),
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

	// Static SAR attributes are the coarse auth gate for Feast REST
	// (/entities, /feature_views, …). kube-rbac-proxy maps GET→get and
	// POST→create on this resource. /projects and /search bypass this gate
	// via --ignore-paths and use server-side SSAR instead.
	//
	// Do not set `rewrites`. kube-rbac-proxy v0.18.1 only supports
	// byQueryParameter and byHttpHeader. An empty rewrite (including the
	// unsupported byHTTPPath key) produces no SAR attributes and the proxy
	// returns HTTP 400 for every authenticated request:
	// "Bad Request. The request or configuration is malformed."
	//
	// Namespace must be set: RoleBindings grant namespaced access. An empty
	// namespace makes SAR cluster-scoped, which would not match the
	// RoleBinding.
	authYaml := `authorization:
  resourceAttributes:
    namespace: ` + cr.Namespace + `
    apiGroup: ` + dataRegistryAPIGroup + `
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
					APIGroups: []string{dataRegistryAPIGroup},
					Resources: dataRegistryPseudoResources,
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
					APIGroups: []string{dataRegistryAPIGroup},
					Resources: dataRegistryPseudoResources,
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

	adminCR := feast.initDataRegistryClusterRole("admin")
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		adminCR,
		func() error {
			adminCR.Labels = map[string]string{
				NameLabelKey:      feast.Handler.FeatureStore.Name,
				ManagedByLabelKey: ManagedByLabelValue,
				"rbac.authorization.k8s.io/aggregate-to-admin": "true",
			}
			adminCR.Rules = []rbacv1.PolicyRule{
				{
					APIGroups: []string{dataRegistryAPIGroup},
					Resources: dataRegistryPseudoResources,
					Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
				},
				{
					APIGroups: []string{dataRegistryAPIGroup},
					Resources: []string{"connections"},
					Verbs:     []string{"use"},
				},
			}
			return nil
		},
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "ClusterRole", adminCR.Name, "operation", op)
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
// using DeleteOwnedFeastObj.
func (feast *FeastServices) CleanupDataRegistryClusterRoles() error {
	for _, suffix := range []string{"viewer", "editor", "admin"} {
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
// Route — ReEncrypt Route exposing the data-registry to external clients
// ---------------------------------------------------------------------------

func (feast *FeastServices) createDataRegistryRoute() error {
	if !isOpenShift {
		return nil
	}
	logger := log.FromContext(feast.Handler.Context)
	route := feast.initDataRegistryRoute()
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		route,
		controllerutil.MutateFn(func() error {
			return feast.setDataRegistryRoute(route)
		}),
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "Route", route.Name, "operation", op)
	}
	return nil
}

func (feast *FeastServices) initDataRegistryRoute() *routev1.Route {
	route := &routev1.Route{
		ObjectMeta: feast.GetObjectMetaType(DataRegistryFeastType),
	}
	route.SetGroupVersionKind(routev1.SchemeGroupVersion.WithKind("Route"))
	return route
}

// setDataRegistryRoute configures a ReEncrypt Route that terminates TLS at the
// OpenShift Router and re-encrypts traffic to the kube-rbac-proxy sidecar.
// The destinationCACertificate is read from the Service CA bundle ConfigMap
// so the Router trusts the internal service certificate.
func (feast *FeastServices) setDataRegistryRoute(route *routev1.Route) error {
	cr := feast.Handler.FeatureStore
	route.Labels = feast.getFeastTypeLabels(DataRegistryFeastType)

	svcName := feast.GetFeastServiceName(DataRegistryFeastType)
	route.Spec = routev1.RouteSpec{
		To: routev1.RouteTargetReference{
			Kind: "Service",
			Name: svcName,
		},
		Port: &routev1.RoutePort{
			TargetPort: intstr.FromInt32(DataRegistryProxyPort),
		},
		TLS: &routev1.TLSConfig{
			Termination:                   routev1.TLSTerminationReencrypt,
			InsecureEdgeTerminationPolicy: routev1.InsecureEdgeTerminationPolicyRedirect,
		},
	}

	caCert := feast.readServiceCACert()
	if caCert != "" {
		route.Spec.TLS.DestinationCACertificate = caCert
	}

	return controllerutil.SetControllerReference(cr, route, feast.Handler.Scheme)
}

// readServiceCACert attempts to read the Service CA certificate from the
// injected CA bundle ConfigMap. Returns empty string if not yet available
// (the ConfigMap is populated asynchronously by OpenShift's service-ca
// controller; the next reconciliation will pick it up).
func (feast *FeastServices) readServiceCACert() string {
	logger := log.FromContext(feast.Handler.Context)
	cm := &corev1.ConfigMap{}
	key := client.ObjectKey{
		Name:      feast.dataRegistryCaBundleCMName(),
		Namespace: feast.Handler.FeatureStore.Namespace,
	}
	if err := feast.Handler.Client.Get(feast.Handler.Context, key, cm); err != nil {
		if !apierrors.IsNotFound(err) {
			logger.Error(err, "Failed to read Service CA bundle ConfigMap", "name", key.Name)
		}
		return ""
	}
	return cm.Data["service-ca.crt"]
}

// ---------------------------------------------------------------------------
// CA Bundle ConfigMap — annotated for OpenShift Service CA injection
// ---------------------------------------------------------------------------

func (feast *FeastServices) deployDataRegistryCaBundleConfigMap() error {
	if !isOpenShift {
		return nil
	}
	logger := log.FromContext(feast.Handler.Context)
	cm := feast.initDataRegistryCaBundleCM()
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		cm,
		controllerutil.MutateFn(func() error {
			return feast.setDataRegistryCaBundleConfigMap(cm)
		}),
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "ConfigMap", cm.Name, "operation", op)
	}
	return nil
}

func (feast *FeastServices) initDataRegistryCaBundleCM() *corev1.ConfigMap {
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      feast.dataRegistryCaBundleCMName(),
			Namespace: feast.Handler.FeatureStore.Namespace,
		},
	}
	cm.SetGroupVersionKind(corev1.SchemeGroupVersion.WithKind("ConfigMap"))
	return cm
}

func (feast *FeastServices) setDataRegistryCaBundleConfigMap(cm *corev1.ConfigMap) error {
	cr := feast.Handler.FeatureStore
	cm.Labels = feast.getFeastTypeLabels(DataRegistryFeastType)
	if cm.Annotations == nil {
		cm.Annotations = map[string]string{}
	}
	cm.Annotations[openshiftInjectCaBundleAnnotation] = stringTrue
	return controllerutil.SetControllerReference(cr, cm, feast.Handler.Scheme)
}

// ---------------------------------------------------------------------------
// Auth Delegator ClusterRoleBinding — grants the SA SubjectAccessReview perms
// ---------------------------------------------------------------------------

// deployDataRegistryAuthDelegatorBinding creates a ClusterRoleBinding that
// binds the feast ServiceAccount to the system:auth-delegator ClusterRole.
// This allows the data-registry-server to perform SubjectAccessReview calls
// for server-side authorization of cross-namespace search requests.
func (feast *FeastServices) deployDataRegistryAuthDelegatorBinding() error {
	logger := log.FromContext(feast.Handler.Context)
	crb := feast.initDataRegistryAuthDelegatorCRB()
	if op, err := controllerutil.CreateOrUpdate(
		feast.Handler.Context,
		feast.Handler.Client,
		crb,
		func() error {
			crb.Labels = map[string]string{
				NameLabelKey:      feast.Handler.FeatureStore.Name,
				ManagedByLabelKey: ManagedByLabelValue,
			}
			crb.Subjects = []rbacv1.Subject{
				{
					Kind:      "ServiceAccount",
					Name:      feast.initFeastSA().Name,
					Namespace: feast.Handler.FeatureStore.Namespace,
				},
			}
			crb.RoleRef = rbacv1.RoleRef{
				APIGroup: rbacv1.GroupName,
				Kind:     "ClusterRole",
				Name:     "system:auth-delegator",
			}
			return nil
		},
	); err != nil {
		return err
	} else if op == controllerutil.OperationResultCreated || op == controllerutil.OperationResultUpdated {
		logger.Info("Successfully reconciled", "ClusterRoleBinding", crb.Name, "operation", op)
	}
	return nil
}

func (feast *FeastServices) initDataRegistryAuthDelegatorCRB() *rbacv1.ClusterRoleBinding {
	crb := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name: feast.dataRegistryAuthDelegatorCRBName(),
		},
	}
	crb.SetGroupVersionKind(rbacv1.SchemeGroupVersion.WithKind("ClusterRoleBinding"))
	return crb
}

// CleanupDataRegistryAuthDelegatorBinding removes the auth-delegator CRB.
// ClusterRoleBindings are cluster-scoped and cannot carry namespace-scoped
// owner references, so we delete by name + managed-by label check.
func (feast *FeastServices) CleanupDataRegistryAuthDelegatorBinding() error {
	crb := &rbacv1.ClusterRoleBinding{}
	name := feast.dataRegistryAuthDelegatorCRBName()
	if err := feast.Handler.Client.Get(
		feast.Handler.Context,
		types.NamespacedName{Name: name},
		crb,
	); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return err
	}
	if crb.Labels[ManagedByLabelKey] == ManagedByLabelValue &&
		crb.Labels[NameLabelKey] == feast.Handler.FeatureStore.Name {
		if err := feast.Handler.Client.Delete(feast.Handler.Context, crb); err != nil && !apierrors.IsNotFound(err) {
			return err
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

func (feast *FeastServices) dataRegistryAuthDelegatorCRBName() string {
	return GetFeastName(feast.Handler.FeatureStore) + dataRegistryAuthDelegatorSuffix
}

func (feast *FeastServices) dataRegistryCaBundleCMName() string {
	return GetFeastName(feast.Handler.FeatureStore) + dataRegistryCaBundleSuffix
}
