/*
Copyright 2026 Feast Community.

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

package controller

import (
	"context"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
	"github.com/feast-dev/feast/infra/feast-operator/internal/controller/access"
	"github.com/feast-dev/feast/infra/feast-operator/internal/controller/handler"
	"github.com/feast-dev/feast/infra/feast-operator/internal/controller/services"
)

// The RHOAI dashboard discovers Feature Stores through the namespace labels set
// by access.EnsureNamespaceLabel. These tests go through the reconciler rather
// than the helper, so they fail if the call is dropped from Reconcile.
var _ = Describe("FeatureStore Controller - namespace labels", func() {
	Context("When a FeatureStore becomes Ready", func() {
		const resourceName = "ns-label-fs"
		const namespaceName = "feast-ns-label-test"

		ctx := context.Background()
		typeNamespacedName := types.NamespacedName{
			Name:      resourceName,
			Namespace: namespaceName,
		}

		BeforeEach(func() {
			By("creating a dedicated namespace without Feast labels")
			ns := &corev1.Namespace{}
			if err := k8sClient.Get(ctx, types.NamespacedName{Name: namespaceName}, ns); err != nil {
				Expect(k8sClient.Create(ctx, &corev1.Namespace{
					ObjectMeta: metav1.ObjectMeta{Name: namespaceName},
				})).To(Succeed())
			}

			By("creating the FeatureStore")
			Expect(k8sClient.Create(ctx, &feastdevv1.FeatureStore{
				ObjectMeta: metav1.ObjectMeta{
					Name:      resourceName,
					Namespace: namespaceName,
				},
				Spec: feastdevv1.FeatureStoreSpec{FeastProject: feastProject},
			})).To(Succeed())
		})

		It("should label the namespace on Ready and remove the Feast label on deletion", func() {
			controllerReconciler := &FeatureStoreReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
			}

			_, err := controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(err).NotTo(HaveOccurred())

			By("marking the deployment available so the FeatureStore becomes Ready")
			resource := &feastdevv1.FeatureStore{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			feast := services.FeastServices{
				Handler: handler.FeastHandler{
					Client:       controllerReconciler.Client,
					Context:      ctx,
					Scheme:       controllerReconciler.Scheme,
					FeatureStore: resource,
				},
			}
			deployment, err := feast.GetDeployment()
			Expect(err).NotTo(HaveOccurred())
			deployment.Status = appsv1.DeploymentStatus{
				Conditions: []appsv1.DeploymentCondition{
					{
						Type:   appsv1.DeploymentAvailable,
						Status: corev1.ConditionTrue,
						Reason: "MinimumReplicasAvailable",
					},
				},
			}
			Expect(controllerReconciler.Status().Update(ctx, &deployment)).To(Succeed())

			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(err).NotTo(HaveOccurred())

			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			Expect(apimeta.IsStatusConditionTrue(resource.Status.Conditions, feastdevv1.ReadyType)).To(BeTrue())

			By("checking the namespace has the Feast and dashboard labels")
			ns := &corev1.Namespace{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: namespaceName}, ns)).To(Succeed())
			Expect(ns.Labels).To(HaveKeyWithValue(access.FeastNamespaceLabelKey, access.FeastNamespaceLabelValue))
			Expect(ns.Labels).To(HaveKeyWithValue(access.DashboardNamespaceLabelKey, access.DashboardNamespaceLabelValue))

			By("deleting the only FeatureStore in the namespace")
			Expect(k8sClient.Delete(ctx, resource)).To(Succeed())
			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(err).NotTo(HaveOccurred())

			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: namespaceName}, ns)).To(Succeed())
			Expect(ns.Labels).NotTo(HaveKey(access.FeastNamespaceLabelKey))
			Expect(ns.Labels).To(HaveKeyWithValue(access.DashboardNamespaceLabelKey, access.DashboardNamespaceLabelValue))
		})
	})
})
