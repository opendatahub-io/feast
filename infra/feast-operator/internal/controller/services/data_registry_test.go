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
	"context"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
	"github.com/feast-dev/feast/infra/feast-operator/internal/controller/handler"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

var _ = Describe("Data Registry", func() {
	var (
		featureStore       *feastdevv1.FeatureStore
		feast              *FeastServices
		typeNamespacedName types.NamespacedName
		ctx                context.Context
	)

	// setAnnotation updates the CR's annotation and refreshes the FeastServices handler.
	setAnnotation := func(value string) {
		if featureStore.Annotations == nil {
			featureStore.Annotations = map[string]string{}
		}
		if value == "" {
			delete(featureStore.Annotations, DataRegistryAnnotation)
		} else {
			featureStore.Annotations[DataRegistryAnnotation] = value
		}
		Expect(k8sClient.Update(ctx, featureStore)).To(Succeed())
		feast.refreshFeatureStore(ctx, typeNamespacedName)
	}

	BeforeEach(func() {
		ctx = context.Background()
		typeNamespacedName = types.NamespacedName{
			Name:      "dr-teststore",
			Namespace: DefaultNs,
		}

		featureStore = &feastdevv1.FeatureStore{
			ObjectMeta: metav1.ObjectMeta{
				Name:      typeNamespacedName.Name,
				Namespace: typeNamespacedName.Namespace,
			},
			Spec: feastdevv1.FeatureStoreSpec{
				FeastProject: "data-registry",
				Services: &feastdevv1.FeatureStoreServices{
					Registry: &feastdevv1.Registry{
						Local: &feastdevv1.LocalRegistryConfig{
							Server: &feastdevv1.RegistryServerConfigs{
								ServerConfigs: feastdevv1.ServerConfigs{
									ContainerConfigs: feastdevv1.ContainerConfigs{
										DefaultCtrConfigs: feastdevv1.DefaultCtrConfigs{
											Image: ptr.To("test-image"),
										},
									},
								},
								GRPC:    ptr.To(true),
								RestAPI: ptr.To(false),
							},
						},
					},
				},
			},
		}

		Expect(k8sClient.Create(ctx, featureStore)).To(Succeed())
		applySpecToStatus(featureStore)

		feast = &FeastServices{
			Handler: handler.FeastHandler{
				Client:       k8sClient,
				Context:      ctx,
				Scheme:       k8sClient.Scheme(),
				FeatureStore: featureStore,
			},
		}
	})

	AfterEach(func() {
		Expect(k8sClient.Delete(ctx, featureStore)).To(Succeed())
	})

	// -------------------------------------------------------------------------
	// isDataRegistryEnabled
	// -------------------------------------------------------------------------

	Describe("isDataRegistryEnabled", func() {
		It("returns false when no annotation is present", func() {
			Expect(feast.isDataRegistryEnabled()).To(BeFalse())
		})

		It("returns true when annotation is set to 'true'", func() {
			setAnnotation("true")
			Expect(feast.isDataRegistryEnabled()).To(BeTrue())
		})

		It("returns false when annotation is set to 'false'", func() {
			setAnnotation("false")
			Expect(feast.isDataRegistryEnabled()).To(BeFalse())
		})

		It("returns false when annotation is set to 'TRUE' (case-sensitive match)", func() {
			setAnnotation("TRUE")
			Expect(feast.isDataRegistryEnabled()).To(BeFalse())
		})
	})

	// -------------------------------------------------------------------------
	// Deployment spec when annotation is enabled
	// -------------------------------------------------------------------------

	Describe("Deployment spec when data registry is enabled", func() {
		BeforeEach(func() {
			setAnnotation("true")
		})

		It("produces the correct Deployment name", func() {
			deploy := feast.initDataRegistryDeploy()
			expectedName := GetFeastName(featureStore) + "-" + string(DataRegistryFeastType)
			Expect(deploy.Name).To(Equal(expectedName))
			Expect(deploy.Namespace).To(Equal(typeNamespacedName.Namespace))
		})

		It("sets labels including service-type data-registry", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())

			Expect(deploy.Labels).To(HaveKeyWithValue(NameLabelKey, featureStore.Name))
			Expect(deploy.Labels).To(HaveKeyWithValue(ManagedByLabelKey, ManagedByLabelValue))
			Expect(deploy.Labels).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))
		})

		It("uses a two-label selector to avoid overlap with the main Deployment", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())

			selector := deploy.Spec.Selector.MatchLabels
			Expect(selector).To(HaveLen(2))
			Expect(selector).To(HaveKeyWithValue(NameLabelKey, featureStore.Name))
			Expect(selector).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))
		})

		It("sets replicas to 1", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			Expect(deploy.Spec.Replicas).NotTo(BeNil())
			Expect(*deploy.Spec.Replicas).To(Equal(int32(1)))
		})

		It("uses the feast ServiceAccount", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			Expect(deploy.Spec.Template.Spec.ServiceAccountName).To(Equal(feast.initFeastSA().Name))
		})

		It("sets an owner reference pointing to the FeatureStore CR", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			Expect(deploy.OwnerReferences).To(HaveLen(1))
			Expect(deploy.OwnerReferences[0].Name).To(Equal(featureStore.Name))
			Expect(*deploy.OwnerReferences[0].Controller).To(BeTrue())
		})

		It("has exactly one container named data-registry-server", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			containers := deploy.Spec.Template.Spec.Containers
			Expect(containers).To(HaveLen(1))
			Expect(containers[0].Name).To(Equal(DataRegistryContainerName))
		})

		It("uses the correct command", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			cmd := deploy.Spec.Template.Spec.Containers[0].Command
			Expect(cmd).To(ContainElements("feast", "serve_registry", "--rest-api"))
		})

		It("exposes port 6572/TCP named 'http'", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			ports := deploy.Spec.Template.Spec.Containers[0].Ports
			Expect(ports).To(HaveLen(1))
			Expect(ports[0].Name).To(Equal("http"))
			Expect(ports[0].ContainerPort).To(Equal(DataRegistryPort))
			Expect(ports[0].Protocol).To(Equal(corev1.ProtocolTCP))
		})

		It("sets all required env vars", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			envs := deploy.Spec.Template.Spec.Containers[0].Env

			envMap := map[string]string{}
			for _, e := range envs {
				envMap[e.Name] = e.Value
			}

			Expect(envMap).To(HaveKey(TmpFeatureStoreYamlEnvVar))
			Expect(envMap[TmpFeatureStoreYamlEnvVar]).NotTo(BeEmpty())
			Expect(envMap).To(HaveKeyWithValue("FEAST_USAGE", "False"))
			Expect(envMap).To(HaveKeyWithValue(DataCatalogEnabledEnvVar, "true"))
			Expect(envMap).To(HaveKeyWithValue(CatalogSSARApiGroupEnvVar, "dataregistry.opendatahub.io"))
			Expect(envMap).To(HaveKey(CatalogSSARResourcesEnvVar))
			Expect(envMap).To(HaveKeyWithValue(FeastProjectEnvVar, featureStore.Spec.FeastProject))
		})

		It("sets readiness probe on /v1/config:6572", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			probe := deploy.Spec.Template.Spec.Containers[0].ReadinessProbe
			Expect(probe).NotTo(BeNil())
			Expect(probe.HTTPGet).NotTo(BeNil())
			Expect(probe.HTTPGet.Path).To(Equal("/v1/config"))
			Expect(probe.HTTPGet.Port).To(Equal(intstr.FromInt32(DataRegistryPort)))
			Expect(probe.PeriodSeconds).To(Equal(int32(10)))
		})

		It("sets liveness probe with failureThreshold 6", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			probe := deploy.Spec.Template.Spec.Containers[0].LivenessProbe
			Expect(probe).NotTo(BeNil())
			Expect(probe.HTTPGet).NotTo(BeNil())
			Expect(probe.HTTPGet.Path).To(Equal("/v1/config"))
			Expect(probe.HTTPGet.Port).To(Equal(intstr.FromInt32(DataRegistryPort)))
			Expect(probe.PeriodSeconds).To(Equal(int32(20)))
			Expect(probe.FailureThreshold).To(Equal(int32(6)))
		})

		It("sets startup probe with failureThreshold 40", func() {
			deploy := feast.initDataRegistryDeploy()
			Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())
			probe := deploy.Spec.Template.Spec.Containers[0].StartupProbe
			Expect(probe).NotTo(BeNil())
			Expect(probe.HTTPGet).NotTo(BeNil())
			Expect(probe.HTTPGet.Path).To(Equal("/v1/config"))
			Expect(probe.HTTPGet.Port).To(Equal(intstr.FromInt32(DataRegistryPort)))
			Expect(probe.PeriodSeconds).To(Equal(int32(3)))
			Expect(probe.FailureThreshold).To(Equal(int32(40)))
		})
	})

	// -------------------------------------------------------------------------
	// deployDataRegistry lifecycle (create, idempotent update, cleanup)
	// -------------------------------------------------------------------------

	Describe("deployDataRegistry lifecycle", func() {
		drDeployKey := func() types.NamespacedName {
			return types.NamespacedName{
				Name:      GetFeastName(featureStore) + "-" + string(DataRegistryFeastType),
				Namespace: typeNamespacedName.Namespace,
			}
		}

		It("does not create a Deployment when annotation is absent", func() {
			Expect(feast.deployDataRegistry()).To(Succeed())

			deploy := &appsv1.Deployment{}
			err := k8sClient.Get(ctx, drDeployKey(), deploy)
			Expect(apierrors.IsNotFound(err)).To(BeTrue())
		})

		It("creates the Deployment when annotation is 'true'", func() {
			setAnnotation("true")
			Expect(feast.deployDataRegistry()).To(Succeed())

			deploy := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, drDeployKey(), deploy)).To(Succeed())
			Expect(deploy.Spec.Template.Spec.Containers).To(HaveLen(1))
			Expect(deploy.Spec.Template.Spec.Containers[0].Name).To(Equal(DataRegistryContainerName))

			// Cleanup for this test
			Expect(k8sClient.Delete(ctx, deploy, client.PropagationPolicy(metav1.DeletePropagationForeground))).To(Succeed())
		})

		It("is idempotent — calling deployDataRegistry twice does not error", func() {
			setAnnotation("true")
			Expect(feast.deployDataRegistry()).To(Succeed())
			Expect(feast.deployDataRegistry()).To(Succeed())

			deploy := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, drDeployKey(), deploy)).To(Succeed())

			// Cleanup for this test
			Expect(k8sClient.Delete(ctx, deploy, client.PropagationPolicy(metav1.DeletePropagationForeground))).To(Succeed())
		})

		It("deletes the Deployment when annotation is removed", func() {
			// Create the Deployment.
			setAnnotation("true")
			Expect(feast.deployDataRegistry()).To(Succeed())
			deploy := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, drDeployKey(), deploy)).To(Succeed())

			// Remove the annotation and reconcile.
			setAnnotation("")
			Expect(feast.deployDataRegistry()).To(Succeed())

			// Deployment should be gone (or have a deletionTimestamp set).
			deployAfter := &appsv1.Deployment{}
			err := k8sClient.Get(ctx, drDeployKey(), deployAfter)
			Expect(err == nil && deployAfter.DeletionTimestamp != nil || apierrors.IsNotFound(err)).To(BeTrue())
		})
	})
})
