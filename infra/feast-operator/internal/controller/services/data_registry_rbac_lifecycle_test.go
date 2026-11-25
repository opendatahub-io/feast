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
	"testing"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
	"github.com/feast-dev/feast/infra/feast-operator/internal/controller/handler"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
)

func TestDataRegistryRBACSurvivesSameNameRecreation(t *testing.T) {
	ctx := context.Background()
	scheme := runtime.NewScheme()
	if err := feastdevv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := rbacv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	cl := fake.NewClientBuilder().WithScheme(scheme).Build()
	makeServices := func(uid types.UID) *FeastServices {
		return &FeastServices{Handler: handler.FeastHandler{
			Client: cl, Context: ctx, Scheme: scheme,
			FeatureStore: &feastdevv1.FeatureStore{ObjectMeta: metav1.ObjectMeta{
				Name: "registry", Namespace: "rhoai-data-registry", UID: uid,
			}},
		}}
	}
	old := makeServices(types.UID("old-instance"))
	newInstance := makeServices(types.UID("new-instance"))
	if err := old.deployDataRegistryClusterRoles(); err != nil {
		t.Fatal(err)
	}
	if err := old.deployDataRegistryAuthDelegatorBinding(); err != nil {
		t.Fatal(err)
	}
	legacyName := old.legacyDataRegistryAuthDelegatorCRBName()
	legacy := &rbacv1.ClusterRoleBinding{ObjectMeta: metav1.ObjectMeta{
		Name: legacyName,
		Labels: map[string]string{
			NameLabelKey: "registry", ManagedByLabelKey: ManagedByLabelValue,
		},
	}}
	if err := cl.Create(ctx, legacy); err != nil {
		t.Fatal(err)
	}
	if err := newInstance.deployDataRegistryClusterRoles(); err != nil {
		t.Fatal(err)
	}
	if err := newInstance.deployDataRegistryAuthDelegatorBinding(); err != nil {
		t.Fatal(err)
	}
	if old.dataRegistryAuthDelegatorCRBName() == newInstance.dataRegistryAuthDelegatorCRBName() {
		t.Fatal("recreated FeatureStore reused the old binding name")
	}
	if err := cl.Get(ctx, types.NamespacedName{Name: legacyName}, &rbacv1.ClusterRoleBinding{}); !apierrors.IsNotFound(err) {
		t.Fatalf("legacy binding was not migrated: %v", err)
	}
	if err := old.CleanupDataRegistryClusterRoles(); err != nil {
		t.Fatal(err)
	}
	if err := old.CleanupDataRegistryAuthDelegatorBinding(); err != nil {
		t.Fatal(err)
	}
	if err := cl.Get(ctx, types.NamespacedName{Name: DataRegistryAdminClusterRoleName}, &rbacv1.ClusterRole{}); err != nil {
		t.Fatalf("new instance ClusterRole was removed: %v", err)
	}
	if err := cl.Get(ctx, types.NamespacedName{Name: newInstance.dataRegistryAuthDelegatorCRBName()}, &rbacv1.ClusterRoleBinding{}); err != nil {
		t.Fatalf("new instance ClusterRoleBinding was removed: %v", err)
	}
	err := cl.Get(ctx, types.NamespacedName{Name: old.dataRegistryAuthDelegatorCRBName()}, &rbacv1.ClusterRoleBinding{})
	if !apierrors.IsNotFound(err) {
		t.Fatalf("old binding was not removed: %v", err)
	}
}

func TestLegacyEditorRoleCleanup(t *testing.T) {
	ctx := context.Background()
	scheme := runtime.NewScheme()
	if err := feastdevv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := rbacv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	cl := fake.NewClientBuilder().WithScheme(scheme).Build()
	feast := &FeastServices{Handler: handler.FeastHandler{
		Client: cl, Context: ctx, Scheme: scheme,
		FeatureStore: &feastdevv1.FeatureStore{ObjectMeta: metav1.ObjectMeta{
			Name: "data-registry", Namespace: "rhoai-data-registry", UID: types.UID("current-instance"),
		}},
	}}
	legacyRole := func(labels map[string]string) *rbacv1.ClusterRole {
		return &rbacv1.ClusterRole{ObjectMeta: metav1.ObjectMeta{
			Name: legacyDataRegistryEditorClusterRoleName, Labels: labels,
		}}
	}
	managedLabels := map[string]string{
		ManagedByLabelKey:            ManagedByLabelValue,
		NameLabelKey:                 "data-registry",
		dataRegistryOwnerUIDLabelKey: "old-instance",
	}

	if err := cl.Create(ctx, legacyRole(managedLabels)); err != nil {
		t.Fatal(err)
	}
	if err := feast.deployDataRegistryClusterRoles(); err != nil {
		t.Fatal(err)
	}
	if err := cl.Get(ctx, types.NamespacedName{Name: legacyDataRegistryEditorClusterRoleName}, &rbacv1.ClusterRole{}); !apierrors.IsNotFound(err) {
		t.Fatalf("stale Feast-managed editor role remains after reconciliation: %v", err)
	}
	if err := feast.deployDataRegistryClusterRoles(); err != nil {
		t.Fatalf("repeat reconciliation failed after editor role removal: %v", err)
	}

	foreignRole := legacyRole(map[string]string{
		ManagedByLabelKey: "another-operator",
		NameLabelKey:      "data-registry",
	})
	if err := cl.Create(ctx, foreignRole); err != nil {
		t.Fatal(err)
	}
	if err := feast.deployDataRegistryClusterRoles(); err != nil {
		t.Fatal(err)
	}
	if err := feast.CleanupDataRegistryClusterRoles(); err != nil {
		t.Fatal(err)
	}
	if err := cl.Get(ctx, types.NamespacedName{Name: legacyDataRegistryEditorClusterRoleName}, &rbacv1.ClusterRole{}); err != nil {
		t.Fatalf("unmanaged editor role was removed: %v", err)
	}
	if err := cl.Delete(ctx, foreignRole); err != nil {
		t.Fatal(err)
	}

	if err := cl.Create(ctx, legacyRole(managedLabels)); err != nil {
		t.Fatal(err)
	}
	if err := feast.CleanupDataRegistryClusterRoles(); err != nil {
		t.Fatal(err)
	}
	if err := cl.Get(ctx, types.NamespacedName{Name: legacyDataRegistryEditorClusterRoleName}, &rbacv1.ClusterRole{}); !apierrors.IsNotFound(err) {
		t.Fatalf("stale Feast-managed editor role remains after cleanup: %v", err)
	}
}

func TestLegacyEditorRoleCleanupRejectsChangedRole(t *testing.T) {
	ctx := context.Background()
	scheme := runtime.NewScheme()
	if err := feastdevv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := rbacv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	base := fake.NewClientBuilder().WithScheme(scheme).Build()
	role := &rbacv1.ClusterRole{ObjectMeta: metav1.ObjectMeta{
		Name: legacyDataRegistryEditorClusterRoleName,
		UID:  types.UID("legacy-role"),
		Labels: map[string]string{
			ManagedByLabelKey: ManagedByLabelValue,
			NameLabelKey:      "data-registry",
		},
	}}
	if err := base.Create(ctx, role); err != nil {
		t.Fatal(err)
	}
	cl := interceptor.NewClient(base, interceptor.Funcs{
		Delete: func(ctx context.Context, underlying client.WithWatch, obj client.Object, opts ...client.DeleteOption) error {
			if obj.GetName() != legacyDataRegistryEditorClusterRoleName {
				return underlying.Delete(ctx, obj, opts...)
			}
			deleteOptions := (&client.DeleteOptions{}).ApplyOptions(opts)
			if deleteOptions.Preconditions == nil || deleteOptions.Preconditions.UID == nil ||
				deleteOptions.Preconditions.ResourceVersion == nil ||
				*deleteOptions.Preconditions.UID != obj.GetUID() ||
				*deleteOptions.Preconditions.ResourceVersion != obj.GetResourceVersion() {
				t.Error("legacy role delete did not use the inspected UID and resource version")
			}
			changed := &rbacv1.ClusterRole{}
			if err := underlying.Get(ctx, client.ObjectKeyFromObject(obj), changed); err != nil {
				return err
			}
			changed.Labels[ManagedByLabelKey] = "another-operator"
			if err := underlying.Update(ctx, changed); err != nil {
				return err
			}
			return underlying.Delete(ctx, obj, opts...)
		},
	})
	feast := &FeastServices{Handler: handler.FeastHandler{
		Client: cl, Context: ctx, Scheme: scheme,
		FeatureStore: &feastdevv1.FeatureStore{ObjectMeta: metav1.ObjectMeta{
			Name: "data-registry", Namespace: "rhoai-data-registry",
		}},
	}}
	if err := feast.cleanupLegacyDataRegistryEditorRole(); !apierrors.IsConflict(err) {
		t.Fatalf("expected conflict after ownership changed, got %v", err)
	}
	remaining := &rbacv1.ClusterRole{}
	if err := base.Get(ctx, types.NamespacedName{Name: role.Name}, remaining); err != nil {
		t.Fatalf("changed role was deleted: %v", err)
	}
	if remaining.Labels[ManagedByLabelKey] != "another-operator" {
		t.Fatalf("expected changed ownership label, got %q", remaining.Labels[ManagedByLabelKey])
	}
}
