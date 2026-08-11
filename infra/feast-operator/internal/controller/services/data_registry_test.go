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
)

var _ = Describe("Data Registry", func() {
	var (
		featureStore       *feastdevv1.FeatureStore
		feast              *FeastServices
		typeNamespacedName types.NamespacedName
		ctx                context.Context
	)

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

	It("is disabled by default and only activates on exact 'true' annotation", func() {
		Expect(feast.isDataRegistryEnabled()).To(BeFalse(), "no annotation")

		for _, v := range []string{"false", "TRUE", "yes", "1"} {
			setAnnotation(v)
			Expect(feast.isDataRegistryEnabled()).To(BeFalse(), "annotation=%q should not enable", v)
		}

		setAnnotation("true")
		Expect(feast.isDataRegistryEnabled()).To(BeTrue())
	})

	It("produces a correct Deployment spec", func() {
		setAnnotation("true")

		deploy := feast.initDataRegistryDeploy()
		Expect(feast.setDataRegistryDeployment(deploy)).To(Succeed())

		expectedName := GetFeastName(featureStore) + "-" + string(DataRegistryFeastType)
		Expect(deploy.Name).To(Equal(expectedName))
		Expect(deploy.Namespace).To(Equal(typeNamespacedName.Namespace))

		// Labels & selector
		Expect(deploy.Labels).To(HaveKeyWithValue(NameLabelKey, featureStore.Name))
		Expect(deploy.Labels).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))
		selector := deploy.Spec.Selector.MatchLabels
		Expect(selector).To(HaveLen(2))
		Expect(selector).To(HaveKeyWithValue(NameLabelKey, featureStore.Name))
		Expect(selector).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))

		Expect(*deploy.Spec.Replicas).To(Equal(int32(1)))
		Expect(deploy.Spec.Template.Spec.ServiceAccountName).To(Equal(feast.initFeastSA().Name))

		// Owner reference
		Expect(deploy.OwnerReferences).To(HaveLen(1))
		Expect(deploy.OwnerReferences[0].Name).To(Equal(featureStore.Name))
		Expect(*deploy.OwnerReferences[0].Controller).To(BeTrue())

		// Container
		Expect(deploy.Spec.Template.Spec.Containers).To(HaveLen(1))
		ctr := deploy.Spec.Template.Spec.Containers[0]
		Expect(ctr.Name).To(Equal(DataRegistryContainerName))
		Expect(ctr.Command).To(ContainElements("feast", "serve_registry", "--rest-api"))
		Expect(ctr.Ports).To(ConsistOf(corev1.ContainerPort{
			Name: "http", ContainerPort: DataRegistryPort, Protocol: corev1.ProtocolTCP,
		}))

		// Env vars
		envMap := map[string]string{}
		for _, e := range ctr.Env {
			envMap[e.Name] = e.Value
		}
		Expect(envMap).To(HaveKey(TmpFeatureStoreYamlEnvVar))
		Expect(envMap[TmpFeatureStoreYamlEnvVar]).NotTo(BeEmpty())
		Expect(envMap).To(HaveKeyWithValue("FEAST_USAGE", "False"))
		Expect(envMap).To(HaveKeyWithValue(DataCatalogEnabledEnvVar, "true"))
		Expect(envMap).To(HaveKeyWithValue(CatalogSSARApiGroupEnvVar, "dataregistry.opendatahub.io"))
		Expect(envMap).To(HaveKey(CatalogSSARResourcesEnvVar))
		Expect(envMap).To(HaveKeyWithValue(FeastProjectEnvVar, featureStore.Spec.FeastProject))

		// Probes — all hit the same endpoint
		expectedProbe := intstr.FromInt32(DataRegistryPort)
		for _, p := range []*corev1.Probe{ctr.ReadinessProbe, ctr.LivenessProbe, ctr.StartupProbe} {
			Expect(p).NotTo(BeNil())
			Expect(p.HTTPGet.Path).To(Equal("/v1/config"))
			Expect(p.HTTPGet.Port).To(Equal(expectedProbe))
		}
	})

	It("creates a real Deployment and cleans it up when the annotation is removed", func() {
		drKey := types.NamespacedName{
			Name:      GetFeastName(featureStore) + "-" + string(DataRegistryFeastType),
			Namespace: typeNamespacedName.Namespace,
		}

		// Disabled → no Deployment
		Expect(feast.deployDataRegistry()).To(Succeed())
		Expect(apierrors.IsNotFound(k8sClient.Get(ctx, drKey, &appsv1.Deployment{}))).To(BeTrue())

		// Enable → Deployment created
		setAnnotation("true")
		Expect(feast.deployDataRegistry()).To(Succeed())
		deploy := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, drKey, deploy)).To(Succeed())
		Expect(deploy.Spec.Template.Spec.Containers[0].Name).To(Equal(DataRegistryContainerName))

		// Idempotent
		Expect(feast.deployDataRegistry()).To(Succeed())

		// Remove annotation → Deployment deleted
		setAnnotation("")
		Expect(feast.deployDataRegistry()).To(Succeed())
		err := k8sClient.Get(ctx, drKey, &appsv1.Deployment{})
		deleted := apierrors.IsNotFound(err)
		if !deleted {
			deploy := &appsv1.Deployment{}
			_ = k8sClient.Get(ctx, drKey, deploy)
			deleted = deploy.DeletionTimestamp != nil
		}
		Expect(deleted).To(BeTrue())
	})
})
