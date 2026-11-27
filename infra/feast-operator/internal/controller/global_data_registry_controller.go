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
	"fmt"
	"reflect"
	"sort"
	"time"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
	"github.com/feast-dev/feast/infra/feast-operator/internal/controller/services"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/selection"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

const (
	GlobalDataRegistryNamespaceLabel = "opendatahub.io/global-data-registry-project"
	globalRegistryManagedLabel       = "opendatahub.io/global-data-registry-managed"
	globalRegistryManagedValue       = "true"
	globalDiscoveryRoleName          = "data-registry-project-discovery"
	globalDiscoveryBindingName       = "data-registry-global-discovery"
	globalViewerBindingName          = "data-registry-global-viewer"
	globalAdminBindingName           = "data-registry-global-admin"
)

var globalAuthGVK = schema.GroupVersionKind{
	Group: "services.platform.opendatahub.io", Version: "v1alpha1", Kind: "Auth",
}

// GlobalDataRegistryReconciler grants catalog access in the one manually
// designated project. It does not own the Auth CR, the Namespace, or any
// administrator-created curator binding.
type GlobalDataRegistryReconciler struct {
	client.Client
	APIReader client.Reader
}

// +kubebuilder:rbac:groups=services.platform.opendatahub.io,resources=auths,verbs=get;list;watch

func (r *GlobalDataRegistryReconciler) Reconcile(ctx context.Context, _ ctrl.Request) (ctrl.Result, error) {
	result := ctrl.Result{RequeueAfter: time.Minute}
	req, _ := labels.NewRequirement(GlobalDataRegistryNamespaceLabel, selection.Exists, nil)
	selector := labels.NewSelector().Add(*req)
	var namespaces corev1.NamespaceList
	if err := r.reader().List(ctx, &namespaces, client.MatchingLabelsSelector{Selector: selector}); err != nil {
		return result, fmt.Errorf("list global Data Registry namespaces: %w", err)
	}

	// A second label is a conflict even when one value is malformed. Revoke all
	// operator-owned grants before returning; never choose an arbitrary project.
	if len(namespaces.Items) > 1 {
		if err := r.cleanup(ctx, ""); err != nil {
			return result, err
		}
		return result, fmt.Errorf("multiple namespaces carry %s", GlobalDataRegistryNamespaceLabel)
	}
	if len(namespaces.Items) == 0 {
		return result, r.cleanup(ctx, "")
	}
	ns := namespaces.Items[0]
	if ns.Labels[GlobalDataRegistryNamespaceLabel] != ns.Name {
		if err := r.cleanup(ctx, ""); err != nil {
			return result, err
		}
		return result, fmt.Errorf("namespace %q has invalid %s value %q: expected namespace name",
			ns.Name, GlobalDataRegistryNamespaceLabel, ns.Labels[GlobalDataRegistryNamespaceLabel])
	}
	if !ns.DeletionTimestamp.IsZero() {
		if err := r.cleanup(ctx, ""); err != nil {
			return result, err
		}
		return result, fmt.Errorf("global Data Registry namespace %q is terminating", ns.Name)
	}

	// The global project has meaning only while the singleton Data Registry
	// FeatureStore is enabled. In particular, remove the discovery grant when
	// the registry CR is deleted so the dropdown does not advertise it.
	active, err := r.dataRegistryFeatureStoreExists(ctx)
	if err != nil {
		return result, err
	}
	if !active {
		return result, r.cleanup(ctx, "")
	}

	allowed, admins, err := r.authGroups(ctx)
	if err != nil {
		// Without a trustworthy Auth read, old grants must not survive a group
		// change. Missing Auth also removes grants, and is retried periodically.
		if cleanupErr := r.cleanup(ctx, ""); cleanupErr != nil {
			return result, cleanupErr
		}
		if apierrors.IsNotFound(err) || apimeta.IsNoMatchError(err) {
			log.FromContext(ctx).Info("Auth is unavailable; withholding global Data Registry grants", "error", err)
			return result, nil
		}
		return result, fmt.Errorf("read platform Auth: %w", err)
	}

	// Remove grants in a previously designated project before granting access
	// in the new one. A manual curator binding is never selected for cleanup.
	if err := r.cleanup(ctx, ns.Name); err != nil {
		return result, err
	}
	if err := r.applyDiscoveryRole(ctx, ns.Name); err != nil {
		return result, err
	}
	if err := r.applyBinding(ctx, ns.Name, globalDiscoveryBindingName,
		rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "Role", Name: globalDiscoveryRoleName}, allowed); err != nil {
		return result, err
	}
	if err := r.applyBinding(ctx, ns.Name, globalViewerBindingName,
		rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: services.DataRegistryViewerClusterRoleName}, allowed); err != nil {
		return result, err
	}
	if err := r.applyBinding(ctx, ns.Name, globalAdminBindingName,
		rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: services.DataRegistryAdminClusterRoleName}, admins); err != nil {
		return result, err
	}
	return result, nil
}

func (r *GlobalDataRegistryReconciler) dataRegistryFeatureStoreExists(ctx context.Context) (bool, error) {
	var stores feastdevv1.FeatureStoreList
	if err := r.reader().List(ctx, &stores); err != nil {
		return false, fmt.Errorf("list Data Registry FeatureStores: %w", err)
	}
	for i := range stores.Items {
		store := &stores.Items[i]
		if store.Name == services.DataRegistryCRName && store.DeletionTimestamp.IsZero() &&
			services.IsDataRegistryCREnabled(store) {
			return true, nil
		}
	}
	return false, nil
}

func (r *GlobalDataRegistryReconciler) authGroups(ctx context.Context) ([]rbacv1.Subject, []rbacv1.Subject, error) {
	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(globalAuthGVK)
	if err := r.reader().Get(ctx, types.NamespacedName{Name: "auth"}, auth); err != nil {
		return nil, nil, err
	}
	allowedGroups, found, err := unstructured.NestedStringSlice(auth.Object, "spec", "allowedGroups")
	if err != nil {
		return nil, nil, fmt.Errorf("read Auth.spec.allowedGroups: %w", err)
	}
	if !found {
		return nil, nil, fmt.Errorf("Auth.spec.allowedGroups is missing")
	}
	adminGroups, found, err := unstructured.NestedStringSlice(auth.Object, "spec", "adminGroups")
	if err != nil {
		return nil, nil, fmt.Errorf("read Auth.spec.adminGroups: %w", err)
	}
	if !found || len(adminGroups) == 0 {
		return nil, nil, fmt.Errorf("Auth.spec.adminGroups is missing or empty")
	}
	for _, group := range append(append([]string{}, allowedGroups...), adminGroups...) {
		if group == "" {
			return nil, nil, fmt.Errorf("Auth contains an empty group")
		}
	}
	for _, group := range adminGroups {
		if group == "system:authenticated" {
			return nil, nil, fmt.Errorf("Auth.adminGroups cannot include system:authenticated")
		}
	}
	return groupSubjects(append(allowedGroups, adminGroups...)), groupSubjects(adminGroups), nil
}

func (r *GlobalDataRegistryReconciler) reader() client.Reader {
	if r.APIReader != nil {
		return r.APIReader
	}
	return r.Client
}

func groupSubjects(groups []string) []rbacv1.Subject {
	set := make(map[string]struct{}, len(groups))
	for _, group := range groups {
		set[group] = struct{}{}
	}
	names := make([]string, 0, len(set))
	for name := range set {
		names = append(names, name)
	}
	sort.Strings(names)
	subjects := make([]rbacv1.Subject, 0, len(names))
	for _, name := range names {
		subjects = append(subjects, rbacv1.Subject{Kind: rbacv1.GroupKind, APIGroup: rbacv1.GroupName, Name: name})
	}
	return subjects
}

func globalRegistryLabels() map[string]string {
	return map[string]string{
		services.ManagedByLabelKey: services.ManagedByLabelValue,
		globalRegistryManagedLabel: globalRegistryManagedValue,
	}
}

func isGlobalRegistryOwned(obj client.Object) bool {
	labels := obj.GetLabels()
	return labels[services.ManagedByLabelKey] == services.ManagedByLabelValue &&
		labels[globalRegistryManagedLabel] == globalRegistryManagedValue
}

func (r *GlobalDataRegistryReconciler) applyDiscoveryRole(ctx context.Context, namespace string) error {
	key := types.NamespacedName{Name: globalDiscoveryRoleName, Namespace: namespace}
	rules := []rbacv1.PolicyRule{{
		APIGroups: []string{""}, Resources: []string{"namespaces"},
		ResourceNames: []string{namespace}, Verbs: []string{"get"},
	}}
	var role rbacv1.Role
	err := r.Get(ctx, key, &role)
	if apierrors.IsNotFound(err) {
		return r.Create(ctx, &rbacv1.Role{
			ObjectMeta: metav1.ObjectMeta{Name: key.Name, Namespace: namespace, Labels: globalRegistryLabels()},
			Rules:      rules,
		})
	}
	if err != nil {
		return err
	}
	if !isGlobalRegistryOwned(&role) {
		return fmt.Errorf("Role %s already exists without global registry ownership", key)
	}
	if reflect.DeepEqual(role.Rules, rules) {
		return nil
	}
	role.Rules = rules
	return r.Update(ctx, &role)
}

func (r *GlobalDataRegistryReconciler) applyBinding(
	ctx context.Context, namespace, name string, roleRef rbacv1.RoleRef, subjects []rbacv1.Subject,
) error {
	key := types.NamespacedName{Name: name, Namespace: namespace}
	var binding rbacv1.RoleBinding
	err := r.Get(ctx, key, &binding)
	if apierrors.IsNotFound(err) {
		return r.Create(ctx, &rbacv1.RoleBinding{
			ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: namespace, Labels: globalRegistryLabels()},
			RoleRef:    roleRef, Subjects: subjects,
		})
	}
	if err != nil {
		return err
	}
	if !isGlobalRegistryOwned(&binding) {
		return fmt.Errorf("RoleBinding %s already exists without global registry ownership", key)
	}
	// RoleRef is immutable in Kubernetes. Recreate an owned binding if it
	// drifted; the next reconciliation creates the desired one.
	if binding.RoleRef != roleRef {
		return r.Delete(ctx, &binding)
	}
	if reflect.DeepEqual(binding.Subjects, subjects) {
		return nil
	}
	binding.Subjects = subjects
	return r.Update(ctx, &binding)
}

func (r *GlobalDataRegistryReconciler) cleanup(ctx context.Context, keepNamespace string) error {
	selector := client.MatchingLabels(globalRegistryLabels())
	var bindings rbacv1.RoleBindingList
	if err := r.List(ctx, &bindings, selector); err != nil {
		return fmt.Errorf("list managed global RoleBindings: %w", err)
	}
	for i := range bindings.Items {
		binding := &bindings.Items[i]
		if binding.Namespace == keepNamespace && isGlobalBindingName(binding.Name) {
			continue
		}
		if isGlobalBindingName(binding.Name) && isGlobalRegistryOwned(binding) {
			if err := client.IgnoreNotFound(r.Delete(ctx, binding)); err != nil {
				return fmt.Errorf("delete RoleBinding %s/%s: %w", binding.Namespace, binding.Name, err)
			}
		}
	}
	var roles rbacv1.RoleList
	if err := r.List(ctx, &roles, selector); err != nil {
		return fmt.Errorf("list managed global Roles: %w", err)
	}
	for i := range roles.Items {
		role := &roles.Items[i]
		if role.Namespace != keepNamespace && role.Name == globalDiscoveryRoleName && isGlobalRegistryOwned(role) {
			if err := client.IgnoreNotFound(r.Delete(ctx, role)); err != nil {
				return fmt.Errorf("delete Role %s/%s: %w", role.Namespace, role.Name, err)
			}
		}
	}
	return nil
}

func isGlobalBindingName(name string) bool {
	return name == globalDiscoveryBindingName || name == globalViewerBindingName || name == globalAdminBindingName
}

func (r *GlobalDataRegistryReconciler) SetupWithManager(mgr ctrl.Manager) error {
	enqueueGlobal := handler.EnqueueRequestsFromMapFunc(func(context.Context, client.Object) []reconcile.Request {
		return []reconcile.Request{{NamespacedName: types.NamespacedName{Name: "global"}}}
	})
	owned := predicate.NewPredicateFuncs(isGlobalRegistryOwned)
	bldr := ctrl.NewControllerManagedBy(mgr).
		For(&corev1.Namespace{}, builder.WithPredicates(globalNamespacePredicate())).
		Watches(&feastdevv1.FeatureStore{}, enqueueGlobal).
		Watches(&rbacv1.Role{}, enqueueGlobal, builder.WithPredicates(owned)).
		Watches(&rbacv1.RoleBinding{}, enqueueGlobal, builder.WithPredicates(owned))

	// Vanilla Feast installs do not have the platform Auth CRD. In RHOAI,
	// watch it for prompt group updates; the Namespace's periodic resync also
	// handles a CRD installed after this controller started.
	if _, err := mgr.GetRESTMapper().RESTMapping(globalAuthGVK.GroupKind(), globalAuthGVK.Version); err == nil {
		auth := &unstructured.Unstructured{}
		auth.SetGroupVersionKind(globalAuthGVK)
		bldr = bldr.Watches(auth, enqueueGlobal)
	} else if !apimeta.IsNoMatchError(err) {
		return fmt.Errorf("discover platform Auth: %w", err)
	}
	return bldr.Complete(r)
}

func globalNamespacePredicate() predicate.Predicate {
	hasLabel := func(obj client.Object) bool {
		_, ok := obj.GetLabels()[GlobalDataRegistryNamespaceLabel]
		return ok
	}
	return predicate.Funcs{
		CreateFunc:  func(e event.CreateEvent) bool { return hasLabel(e.Object) },
		DeleteFunc:  func(e event.DeleteEvent) bool { return hasLabel(e.Object) },
		UpdateFunc:  func(e event.UpdateEvent) bool { return hasLabel(e.ObjectOld) || hasLabel(e.ObjectNew) },
		GenericFunc: func(e event.GenericEvent) bool { return hasLabel(e.Object) },
	}
}
