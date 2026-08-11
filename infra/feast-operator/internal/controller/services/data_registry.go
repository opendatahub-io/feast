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
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
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

// deployDataRegistry creates (or updates) the dedicated data-registry Deployment
// when the catalog annotation is present, and deletes it when the annotation is
// removed or absent.
func (feast *FeastServices) deployDataRegistry() error {
	if !feast.isDataRegistryEnabled() {
		// Cleanup: no-op if the Deployment never existed, delete if it did.
		return feast.Handler.DeleteOwnedFeastObj(feast.initDataRegistryDeploy())
	}

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

// initDataRegistryDeploy returns a skeleton Deployment whose ObjectMeta is set
// to feast-<cr.Name>-data-registry in the CR's namespace.
func (feast *FeastServices) initDataRegistryDeploy() *appsv1.Deployment {
	deploy := &appsv1.Deployment{
		ObjectMeta: feast.GetObjectMetaType(DataRegistryFeastType),
	}
	deploy.SetGroupVersionKind(appsv1.SchemeGroupVersion.WithKind("Deployment"))
	return deploy
}

// setDataRegistryDeployment mutates deploy with the full data-registry Deployment
// spec. Called by CreateOrUpdate on every reconcile.
func (feast *FeastServices) setDataRegistryDeployment(deploy *appsv1.Deployment) error {
	cr := feast.Handler.FeatureStore

	// Full label set for the Deployment and pod template.
	labels := feast.getFeastTypeLabels(DataRegistryFeastType)

	// The selector uses both name and service-type to avoid overlapping with the
	// main Deployment's selector ({feast.dev/name: cr.Name} only).
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
			},
		},
	}

	ctr, err := feast.buildDataRegistryContainer()
	if err != nil {
		return err
	}
	deploy.Spec.Template.Spec.Containers = []corev1.Container{ctr}

	return controllerutil.SetControllerReference(cr, deploy, feast.Handler.Scheme)
}

// buildDataRegistryContainer returns the data-registry-server container spec.
func (feast *FeastServices) buildDataRegistryContainer() (corev1.Container, error) {
	cr := feast.Handler.FeatureStore

	image := getFeatureServerImage()

	fsYamlB64, err := feast.GetServiceFeatureStoreYamlBase64()
	if err != nil {
		return corev1.Container{}, err
	}

	probeHandler := corev1.ProbeHandler{
		HTTPGet: &corev1.HTTPGetAction{
			Path: "/v1/config",
			Port: intstr.FromInt32(DataRegistryPort),
		},
	}

	return corev1.Container{
		Name:  DataRegistryContainerName,
		Image: image,
		Command: []string{
			feastCommand,
			"serve_registry",
			"--rest-api",
			"-p", "6572",
		},
		Ports: []corev1.ContainerPort{
			{
				Name:          "http",
				ContainerPort: DataRegistryPort,
				Protocol:      corev1.ProtocolTCP,
			},
		},
		Env: []corev1.EnvVar{
			{Name: TmpFeatureStoreYamlEnvVar, Value: fsYamlB64},
			{Name: "FEAST_USAGE", Value: "False"},
			{Name: DataCatalogEnabledEnvVar, Value: "true"},
			{Name: CatalogSSARApiGroupEnvVar, Value: "dataregistry.opendatahub.io"},
			{Name: CatalogSSARResourcesEnvVar, Value: ""},
			{Name: FeastProjectEnvVar, Value: cr.Status.Applied.FeastProject},
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
