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

package controller

import (
	"context"
	"reflect"
	"testing"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
	"github.com/feast-dev/feast/infra/feast-operator/internal/controller/services"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func newGlobalRegistryTestReconciler(t *testing.T, namespaces ...*corev1.Namespace) (*GlobalDataRegistryReconciler, client.Client) {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := rbacv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := feastdevv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	scheme.AddKnownTypeWithName(globalAuthGVK, &unstructured.Unstructured{})
	objects := make([]client.Object, 0, len(namespaces)+1)
	for _, ns := range namespaces {
		objects = append(objects, ns)
	}
	auth := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": globalAuthGVK.GroupVersion().String(),
		"kind":       "Auth",
		"metadata":   map[string]any{"name": "auth"},
		"spec": map[string]any{
			"allowedGroups": []any{"analysts", "shared", "analysts"},
			"adminGroups":   []any{"admins", "shared"},
		},
	}}
	objects = append(objects, auth)
	objects = append(objects, &feastdevv1.FeatureStore{ObjectMeta: metav1.ObjectMeta{
		Name: services.DataRegistryCRName, Namespace: "rhoai-data-registry",
		Annotations: map[string]string{services.DataRegistryAnnotation: "true"},
	}})
	cl := fake.NewClientBuilder().WithScheme(scheme).WithObjects(objects...).Build()
	return &GlobalDataRegistryReconciler{Client: cl, APIReader: cl}, cl
}

func labeledGlobalNamespace(name string) *corev1.Namespace {
	return &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{
		Name: name, Labels: map[string]string{GlobalDataRegistryNamespaceLabel: name},
	}}
}

func reconcileGlobalRegistry(t *testing.T, r *GlobalDataRegistryReconciler) error {
	t.Helper()
	_, err := r.Reconcile(context.Background(), ctrl.Request{})
	return err
}

func getGlobalBinding(t *testing.T, cl client.Client, namespace, name string) *rbacv1.RoleBinding {
	t.Helper()
	var binding rbacv1.RoleBinding
	if err := cl.Get(context.Background(), types.NamespacedName{Namespace: namespace, Name: name}, &binding); err != nil {
		t.Fatal(err)
	}
	return &binding
}

func assertGlobalResourcesAbsent(t *testing.T, cl client.Client, namespace string) {
	t.Helper()
	ctx := context.Background()
	for _, name := range []string{globalViewerBindingName, globalAdminBindingName, globalDiscoveryBindingName} {
		if err := cl.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, &rbacv1.RoleBinding{}); !apierrors.IsNotFound(err) {
			t.Errorf("RoleBinding %s/%s: want NotFound, got %v", namespace, name, err)
		}
	}
	if err := cl.Get(ctx, types.NamespacedName{Namespace: namespace, Name: globalDiscoveryRoleName}, &rbacv1.Role{}); !apierrors.IsNotFound(err) {
		t.Errorf("discovery Role %s: want NotFound, got %v", namespace, err)
	}
}

func TestGlobalRegistryGrantsAndRevokesOnManualLabelChange(t *testing.T) {
	ns := labeledGlobalNamespace("global-ai-assets")
	r, cl := newGlobalRegistryTestReconciler(t, ns)
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	var discovery rbacv1.Role
	if err := cl.Get(ctx, types.NamespacedName{Namespace: ns.Name, Name: globalDiscoveryRoleName}, &discovery); err != nil {
		t.Fatal(err)
	}
	wantRules := []rbacv1.PolicyRule{{
		APIGroups: []string{""}, Resources: []string{"namespaces"},
		ResourceNames: []string{ns.Name}, Verbs: []string{"get"},
	}}
	if !reflect.DeepEqual(discovery.Rules, wantRules) {
		t.Fatalf("discovery rules = %#v, want %#v", discovery.Rules, wantRules)
	}
	viewer := getGlobalBinding(t, cl, ns.Name, globalViewerBindingName)
	if viewer.RoleRef.Name != services.DataRegistryViewerClusterRoleName {
		t.Fatalf("viewer RoleRef = %#v", viewer.RoleRef)
	}
	if got := []string{viewer.Subjects[0].Name, viewer.Subjects[1].Name, viewer.Subjects[2].Name}; !reflect.DeepEqual(got, []string{"admins", "analysts", "shared"}) {
		t.Fatalf("viewer subjects = %v", got)
	}
	admin := getGlobalBinding(t, cl, ns.Name, globalAdminBindingName)
	if admin.RoleRef.Name != services.DataRegistryAdminClusterRoleName {
		t.Fatalf("admin RoleRef = %#v", admin.RoleRef)
	}
	if got := []string{admin.Subjects[0].Name, admin.Subjects[1].Name}; !reflect.DeepEqual(got, []string{"admins", "shared"}) {
		t.Fatalf("admin subjects = %v", got)
	}
	discoveryBinding := getGlobalBinding(t, cl, ns.Name, globalDiscoveryBindingName)
	if discoveryBinding.RoleRef.Kind != "Role" || discoveryBinding.RoleRef.Name != globalDiscoveryRoleName ||
		!reflect.DeepEqual(discoveryBinding.Subjects, viewer.Subjects) {
		t.Fatalf("discovery binding = %#v", discoveryBinding)
	}
	viewerVersion := viewer.ResourceVersion
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	if got := getGlobalBinding(t, cl, ns.Name, globalViewerBindingName).ResourceVersion; got != viewerVersion {
		t.Fatalf("unchanged reconcile updated viewer binding: %s -> %s", viewerVersion, got)
	}

	// A curator binding created by an administrator is outside this controller's ownership.
	manual := &rbacv1.RoleBinding{ObjectMeta: metav1.ObjectMeta{Name: "global-registry-curators", Namespace: ns.Name},
		RoleRef: rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: services.DataRegistryAdminClusterRoleName}}
	if err := cl.Create(ctx, manual); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	current := &corev1.Namespace{}
	if err := cl.Get(ctx, types.NamespacedName{Name: ns.Name}, current); err != nil {
		t.Fatal(err)
	}
	delete(current.Labels, GlobalDataRegistryNamespaceLabel)
	if err := cl.Update(ctx, current); err != nil {
		t.Fatal(err)
	}
	// Simulate a restart after the label was removed while the operator was down.
	restarted := &GlobalDataRegistryReconciler{Client: cl, APIReader: cl}
	if err := reconcileGlobalRegistry(t, restarted); err != nil {
		t.Fatal(err)
	}
	assertGlobalResourcesAbsent(t, cl, ns.Name)
	if err := cl.Get(ctx, types.NamespacedName{Namespace: ns.Name, Name: manual.Name}, &rbacv1.RoleBinding{}); err != nil {
		t.Fatalf("manual curator binding was removed: %v", err)
	}
	// The same relist restores grants after a human adds the label again.
	if current.Labels == nil {
		current.Labels = map[string]string{}
	}
	current.Labels[GlobalDataRegistryNamespaceLabel] = current.Name
	if err := cl.Update(ctx, current); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	getGlobalBinding(t, cl, ns.Name, globalViewerBindingName)
}

func TestGlobalRegistryFailsClosedForTwoLabelsAndRecovers(t *testing.T) {
	first := labeledGlobalNamespace("global-one")
	second := labeledGlobalNamespace("global-two")
	r, cl := newGlobalRegistryTestReconciler(t, first)
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	if err := cl.Create(context.Background(), second); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err == nil {
		t.Fatal("two labeled namespaces should report a conflict")
	}
	assertGlobalResourcesAbsent(t, cl, first.Name)
	assertGlobalResourcesAbsent(t, cl, second.Name)

	// A fresh reconciler sees the conflict from a relist, without relying on a watch event.
	restarted := &GlobalDataRegistryReconciler{Client: cl, APIReader: cl}
	if err := reconcileGlobalRegistry(t, restarted); err == nil {
		t.Fatal("startup relist should report a conflict")
	}
	current := &corev1.Namespace{}
	if err := cl.Get(context.Background(), types.NamespacedName{Name: second.Name}, current); err != nil {
		t.Fatal(err)
	}
	delete(current.Labels, GlobalDataRegistryNamespaceLabel)
	if err := cl.Update(context.Background(), current); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, restarted); err != nil {
		t.Fatal(err)
	}
	getGlobalBinding(t, cl, first.Name, globalViewerBindingName)
	assertGlobalResourcesAbsent(t, cl, second.Name)
}

func TestGlobalRegistryAuthChangeAndDrift(t *testing.T) {
	ns := labeledGlobalNamespace("global-ai-assets")
	r, cl := newGlobalRegistryTestReconciler(t, ns)
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(globalAuthGVK)
	if err := cl.Get(ctx, types.NamespacedName{Name: "auth"}, auth); err != nil {
		t.Fatal(err)
	}
	if err := unstructured.SetNestedStringSlice(auth.Object, []string{"new-viewers"}, "spec", "allowedGroups"); err != nil {
		t.Fatal(err)
	}
	if err := unstructured.SetNestedStringSlice(auth.Object, []string{"new-admins"}, "spec", "adminGroups"); err != nil {
		t.Fatal(err)
	}
	if err := cl.Update(ctx, auth); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	viewer := getGlobalBinding(t, cl, ns.Name, globalViewerBindingName)
	if got := []string{viewer.Subjects[0].Name, viewer.Subjects[1].Name}; !reflect.DeepEqual(got, []string{"new-admins", "new-viewers"}) {
		t.Fatalf("updated viewer subjects = %v", got)
	}
	admin := getGlobalBinding(t, cl, ns.Name, globalAdminBindingName)
	if len(admin.Subjects) != 1 || admin.Subjects[0].Name != "new-admins" {
		t.Fatalf("updated admin subjects = %#v", admin.Subjects)
	}
	if err := cl.Delete(ctx, viewer); err != nil {
		t.Fatal(err)
	}
	var role rbacv1.Role
	if err := cl.Get(ctx, types.NamespacedName{Namespace: ns.Name, Name: globalDiscoveryRoleName}, &role); err != nil {
		t.Fatal(err)
	}
	role.Rules[0].Verbs = []string{"list"}
	if err := cl.Update(ctx, &role); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	getGlobalBinding(t, cl, ns.Name, globalViewerBindingName)
	if err := cl.Get(ctx, types.NamespacedName{Namespace: ns.Name, Name: globalDiscoveryRoleName}, &role); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(role.Rules[0].Verbs, []string{"get"}) {
		t.Fatalf("discovery Role drift was not repaired: %#v", role.Rules)
	}

	// An invalid Auth never leaves old group grants active.
	if err := unstructured.SetNestedStringSlice(auth.Object, []string{"system:authenticated"}, "spec", "adminGroups"); err != nil {
		t.Fatal(err)
	}
	if err := cl.Update(ctx, auth); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err == nil {
		t.Fatal("unsafe admin group should fail")
	}
	assertGlobalResourcesAbsent(t, cl, ns.Name)
}

func TestGlobalRegistryDoesNotAdoptAnUnownedBinding(t *testing.T) {
	ns := labeledGlobalNamespace("global-ai-assets")
	r, cl := newGlobalRegistryTestReconciler(t, ns)
	unowned := &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: globalViewerBindingName, Namespace: ns.Name},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: "other-role"},
	}
	if err := cl.Create(context.Background(), unowned); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err == nil {
		t.Fatal("unowned binding collision should fail")
	}
	got := getGlobalBinding(t, cl, ns.Name, globalViewerBindingName)
	if got.RoleRef.Name != "other-role" {
		t.Fatalf("unowned RoleBinding was changed: %#v", got.RoleRef)
	}
}

func TestGlobalRegistryWithholdsGrantsWithoutValidDesignationOrAuth(t *testing.T) {
	ns := labeledGlobalNamespace("global-ai-assets")
	r, cl := newGlobalRegistryTestReconciler(t, ns)
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	current := &corev1.Namespace{}
	if err := cl.Get(ctx, types.NamespacedName{Name: ns.Name}, current); err != nil {
		t.Fatal(err)
	}
	current.Labels[GlobalDataRegistryNamespaceLabel] = "wrong-name"
	if err := cl.Update(ctx, current); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err == nil {
		t.Fatal("malformed designation should report an error")
	}
	assertGlobalResourcesAbsent(t, cl, ns.Name)

	current.Labels[GlobalDataRegistryNamespaceLabel] = current.Name
	if err := cl.Update(ctx, current); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(globalAuthGVK)
	if err := cl.Get(ctx, types.NamespacedName{Name: "auth"}, auth); err != nil {
		t.Fatal(err)
	}
	if err := cl.Delete(ctx, auth); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatalf("missing Auth should withhold grants and retry: %v", err)
	}
	assertGlobalResourcesAbsent(t, cl, ns.Name)
}

func TestGlobalRegistryMovesOnlyOperatorOwnedGrants(t *testing.T) {
	first := labeledGlobalNamespace("global-one")
	second := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: "global-two"}}
	r, cl := newGlobalRegistryTestReconciler(t, first, second)
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	firstCurrent := &corev1.Namespace{}
	secondCurrent := &corev1.Namespace{}
	if err := cl.Get(ctx, types.NamespacedName{Name: first.Name}, firstCurrent); err != nil {
		t.Fatal(err)
	}
	if err := cl.Get(ctx, types.NamespacedName{Name: second.Name}, secondCurrent); err != nil {
		t.Fatal(err)
	}
	delete(firstCurrent.Labels, GlobalDataRegistryNamespaceLabel)
	if err := cl.Update(ctx, firstCurrent); err != nil {
		t.Fatal(err)
	}
	secondCurrent.Labels = map[string]string{GlobalDataRegistryNamespaceLabel: second.Name}
	if err := cl.Update(ctx, secondCurrent); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	assertGlobalResourcesAbsent(t, cl, first.Name)
	getGlobalBinding(t, cl, second.Name, globalViewerBindingName)
	var discovery rbacv1.Role
	if err := cl.Get(ctx, types.NamespacedName{Namespace: second.Name, Name: globalDiscoveryRoleName}, &discovery); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(discovery.Rules[0].ResourceNames, []string{second.Name}) {
		t.Fatalf("moved discovery Role still names old project: %#v", discovery.Rules)
	}
}

func TestGlobalRegistryRevokesGrantsWhenRegistryIsDisabled(t *testing.T) {
	ns := labeledGlobalNamespace("global-ai-assets")
	r, cl := newGlobalRegistryTestReconciler(t, ns)
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	var registry feastdevv1.FeatureStore
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "rhoai-data-registry", Name: services.DataRegistryCRName}, &registry); err != nil {
		t.Fatal(err)
	}
	delete(registry.Annotations, services.DataRegistryAnnotation)
	if err := cl.Update(ctx, &registry); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	assertGlobalResourcesAbsent(t, cl, ns.Name)
}

func TestGlobalRegistryRevokesGrantsWhenNamespaceTerminates(t *testing.T) {
	ns := labeledGlobalNamespace("global-ai-assets")
	ns.Finalizers = []string{"test.example/finalizer"}
	r, cl := newGlobalRegistryTestReconciler(t, ns)
	if err := reconcileGlobalRegistry(t, r); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	current := &corev1.Namespace{}
	if err := cl.Get(ctx, types.NamespacedName{Name: ns.Name}, current); err != nil {
		t.Fatal(err)
	}
	if err := cl.Delete(ctx, current); err != nil {
		t.Fatal(err)
	}
	if err := reconcileGlobalRegistry(t, r); err == nil {
		t.Fatal("terminating global namespace should report an error")
	}
	assertGlobalResourcesAbsent(t, cl, ns.Name)
}
