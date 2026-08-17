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
	routev1 "github.com/openshift/api/route/v1"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
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

	It("produces a two-container Deployment with localhost binding, --ignore-paths, and empty FEAST_PROJECT", func() {
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

		// Must have exactly 2 containers: feast-server + kube-rbac-proxy
		Expect(deploy.Spec.Template.Spec.Containers).To(HaveLen(2))

		// --- feast-server container ---
		feastCtr := deploy.Spec.Template.Spec.Containers[0]
		Expect(feastCtr.Name).To(Equal(DataRegistryContainerName))

		// Localhost binding: -h 127.0.0.1
		Expect(feastCtr.Command).To(ContainElements("feast", "serve_registry", "--rest-api", "-h", DataRegistryLocalhostAddr))

		// No container ports exposed externally (traffic goes through proxy)
		Expect(feastCtr.Ports).To(BeEmpty())

		// Env vars
		envMap := map[string]string{}
		for _, e := range feastCtr.Env {
			envMap[e.Name] = e.Value
		}
		Expect(envMap).To(HaveKey(TmpFeatureStoreYamlEnvVar))
		Expect(envMap[TmpFeatureStoreYamlEnvVar]).NotTo(BeEmpty())
		Expect(envMap).To(HaveKeyWithValue("FEAST_USAGE", "False"))
		Expect(envMap).To(HaveKeyWithValue(DataCatalogEnabledEnvVar, "true"))
		Expect(envMap).To(HaveKeyWithValue(CatalogSSARApiGroupEnvVar, "dataregistry.opendatahub.io"))
		Expect(envMap).To(HaveKeyWithValue(CatalogSSARResourcesEnvVar, "namespaces,tables,volumes,generic-tables"))
		// Multi-tenancy: FEAST_PROJECT must be empty for dynamic routing
		Expect(envMap).To(HaveKeyWithValue(FeastProjectEnvVar, ""))

		// Probes target localhost
		expectedProbe := intstr.FromInt32(DataRegistryPort)
		for _, p := range []*corev1.Probe{feastCtr.ReadinessProbe, feastCtr.LivenessProbe, feastCtr.StartupProbe} {
			Expect(p).NotTo(BeNil())
			Expect(p.HTTPGet.Path).To(Equal("/v1/config"))
			Expect(p.HTTPGet.Port).To(Equal(expectedProbe))
			Expect(p.HTTPGet.Host).To(Equal(DataRegistryLocalhostAddr))
		}

		// --- kube-rbac-proxy container ---
		proxyCtr := deploy.Spec.Template.Spec.Containers[1]
		Expect(proxyCtr.Name).To(Equal(DataRegistryProxyContainerName))
		Expect(proxyCtr.Image).To(Equal(DefaultKubeRBACProxyImage))
		Expect(proxyCtr.Args).To(ContainElements(
			"--secure-listen-address=0.0.0.0:8443",
			"--upstream=http://127.0.0.1:6572/",
			"--config-file=/etc/kube-rbac-proxy/auth.yaml",
			"--tls-cert-file=/etc/tls/tls.crt",
			"--tls-private-key-file=/etc/tls/tls.key",
			"--ignore-paths=/v1/search",
		))
		Expect(proxyCtr.Ports).To(ConsistOf(corev1.ContainerPort{
			Name: "https", ContainerPort: DataRegistryProxyPort, Protocol: corev1.ProtocolTCP,
		}))

		// Proxy volume mounts
		Expect(proxyCtr.VolumeMounts).To(ContainElements(
			corev1.VolumeMount{Name: "auth-config", MountPath: "/etc/kube-rbac-proxy", ReadOnly: true},
			corev1.VolumeMount{Name: "tls-certs", MountPath: "/etc/tls", ReadOnly: true},
		))

		// Proxy probes
		Expect(proxyCtr.ReadinessProbe).NotTo(BeNil())
		Expect(proxyCtr.ReadinessProbe.TCPSocket.Port).To(Equal(intstr.FromInt32(DataRegistryProxyPort)))
		Expect(proxyCtr.LivenessProbe).NotTo(BeNil())
		Expect(proxyCtr.LivenessProbe.TCPSocket.Port).To(Equal(intstr.FromInt32(DataRegistryProxyPort)))

		// Pod volumes
		volumes := deploy.Spec.Template.Spec.Volumes
		Expect(volumes).To(HaveLen(2))

		var authVol, tlsVol *corev1.Volume
		for i := range volumes {
			switch volumes[i].Name {
			case "auth-config":
				authVol = &volumes[i]
			case "tls-certs":
				tlsVol = &volumes[i]
			}
		}
		Expect(authVol).NotTo(BeNil())
		Expect(authVol.ConfigMap.Name).To(Equal(feast.dataRegistryAuthCMName()))
		Expect(tlsVol).NotTo(BeNil())
		Expect(tlsVol.Secret.SecretName).To(Equal(feast.dataRegistryTlsSecretName()))
	})

	It("creates an HTTPS Service targeting the proxy port", func() {
		setAnnotation("true")

		svc := feast.initDataRegistrySvc()
		Expect(feast.setDataRegistryService(svc)).To(Succeed())

		Expect(svc.Labels).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))

		// OpenShift serving cert annotation
		Expect(svc.Annotations).To(HaveKey(openshiftServingCertSecretAnnotation))
		Expect(svc.Annotations[openshiftServingCertSecretAnnotation]).To(Equal(feast.dataRegistryTlsSecretName()))

		// Service spec
		Expect(svc.Spec.Type).To(Equal(corev1.ServiceTypeClusterIP))
		Expect(svc.Spec.Selector).To(HaveKeyWithValue(NameLabelKey, featureStore.Name))
		Expect(svc.Spec.Selector).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))
		Expect(svc.Spec.Ports).To(HaveLen(1))
		Expect(svc.Spec.Ports[0].Port).To(Equal(int32(HttpsPort)))
		Expect(svc.Spec.Ports[0].TargetPort).To(Equal(intstr.FromInt32(DataRegistryProxyPort)))
		Expect(svc.Spec.Ports[0].Name).To(Equal(HttpsScheme))

		// Owner reference
		Expect(svc.OwnerReferences).To(HaveLen(1))
		Expect(svc.OwnerReferences[0].Name).To(Equal(featureStore.Name))
	})

	It("creates an auth.yaml ConfigMap with per-resource regex mapping", func() {
		setAnnotation("true")

		cm := feast.initDataRegistryAuthCM()
		Expect(feast.setDataRegistryAuthConfig(cm)).To(Succeed())

		Expect(cm.Labels).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))
		Expect(cm.Data).To(HaveKey("auth.yaml"))

		authContent := cm.Data["auth.yaml"]
		Expect(authContent).To(ContainSubstring("dataregistry.opendatahub.io"))
		Expect(authContent).To(ContainSubstring("registries"))
		// Regex path-based resource rewrites
		Expect(authContent).To(ContainSubstring("rewrites"))
		Expect(authContent).To(ContainSubstring("byHTTPPath"))
		Expect(authContent).To(ContainSubstring("tables"))
		Expect(authContent).To(ContainSubstring("volumes"))
		Expect(authContent).To(ContainSubstring("generic-tables"))
		Expect(authContent).To(ContainSubstring("namespaces"))

		// Owner reference
		Expect(cm.OwnerReferences).To(HaveLen(1))
		Expect(cm.OwnerReferences[0].Name).To(Equal(featureStore.Name))
	})

	It("produces a ReEncrypt Route spec targeting the proxy Service", func() {
		setAnnotation("true")

		route := feast.initDataRegistryRoute()
		Expect(feast.setDataRegistryRoute(route)).To(Succeed())

		expectedSvcName := feast.GetFeastServiceName(DataRegistryFeastType)
		Expect(route.Labels).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))
		Expect(route.Spec.To.Kind).To(Equal("Service"))
		Expect(route.Spec.To.Name).To(Equal(expectedSvcName))
		Expect(route.Spec.Port.TargetPort).To(Equal(intstr.FromInt32(DataRegistryProxyPort)))

		Expect(route.Spec.TLS).NotTo(BeNil())
		Expect(route.Spec.TLS.Termination).To(Equal(routev1.TLSTerminationReencrypt))
		Expect(route.Spec.TLS.InsecureEdgeTerminationPolicy).To(Equal(routev1.InsecureEdgeTerminationPolicyRedirect))

		// Without a populated CA bundle CM, DestinationCACertificate is empty
		Expect(route.Spec.TLS.DestinationCACertificate).To(BeEmpty())

		// Owner reference
		Expect(route.OwnerReferences).To(HaveLen(1))
		Expect(route.OwnerReferences[0].Name).To(Equal(featureStore.Name))
	})

	It("populates DestinationCACertificate when CA bundle ConfigMap exists", func() {
		setAnnotation("true")

		// Simulate OpenShift injecting the service CA into the bundle CM
		bundleCM := &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{
				Name:      feast.dataRegistryCaBundleCMName(),
				Namespace: typeNamespacedName.Namespace,
			},
			Data: map[string]string{
				"service-ca.crt": "-----BEGIN CERTIFICATE-----\nFAKECA\n-----END CERTIFICATE-----\n",
			},
		}
		Expect(k8sClient.Create(ctx, bundleCM)).To(Succeed())

		route := feast.initDataRegistryRoute()
		Expect(feast.setDataRegistryRoute(route)).To(Succeed())
		Expect(route.Spec.TLS.DestinationCACertificate).To(ContainSubstring("FAKECA"))

		Expect(k8sClient.Delete(ctx, bundleCM)).To(Succeed())
	})

	It("produces a CA bundle ConfigMap with inject-cabundle annotation", func() {
		setAnnotation("true")

		cm := feast.initDataRegistryCaBundleCM()
		Expect(feast.setDataRegistryCaBundleConfigMap(cm)).To(Succeed())

		Expect(cm.Labels).To(HaveKeyWithValue(ServiceTypeLabelKey, string(DataRegistryFeastType)))
		Expect(cm.Annotations).To(HaveKeyWithValue(openshiftInjectCaBundleAnnotation, stringTrue))

		// Owner reference
		Expect(cm.OwnerReferences).To(HaveLen(1))
		Expect(cm.OwnerReferences[0].Name).To(Equal(featureStore.Name))
	})

	It("produces an auth-delegator ClusterRoleBinding for server-side SSAR", func() {
		setAnnotation("true")

		Expect(feast.deployDataRegistryAuthDelegatorBinding()).To(Succeed())

		crb := &rbacv1.ClusterRoleBinding{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryAuthDelegatorCRBName()}, crb)).To(Succeed())

		Expect(crb.RoleRef.APIGroup).To(Equal(rbacv1.GroupName))
		Expect(crb.RoleRef.Kind).To(Equal("ClusterRole"))
		Expect(crb.RoleRef.Name).To(Equal("system:auth-delegator"))

		Expect(crb.Subjects).To(HaveLen(1))
		Expect(crb.Subjects[0].Kind).To(Equal("ServiceAccount"))
		Expect(crb.Subjects[0].Name).To(Equal(feast.initFeastSA().Name))
		Expect(crb.Subjects[0].Namespace).To(Equal(typeNamespacedName.Namespace))

		Expect(crb.Labels).To(HaveKeyWithValue(ManagedByLabelKey, ManagedByLabelValue))

		// Cleanup
		Expect(feast.deleteDataRegistryAuthDelegatorBinding()).To(Succeed())
		err := k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryAuthDelegatorCRBName()}, &rbacv1.ClusterRoleBinding{})
		Expect(apierrors.IsNotFound(err)).To(BeTrue())
	})

	It("creates three ClusterRoles: viewer, editor, and admin", func() {
		setAnnotation("true")

		Expect(feast.deployDataRegistryClusterRoles()).To(Succeed())

		// Admin ClusterRole
		adminCR := &rbacv1.ClusterRole{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryClusterRoleName("admin")}, adminCR)).To(Succeed())
		Expect(adminCR.Labels).To(HaveKeyWithValue("rbac.authorization.k8s.io/aggregate-to-admin", "true"))
		Expect(adminCR.Labels).NotTo(HaveKey("rbac.authorization.k8s.io/aggregate-to-view"))
		Expect(adminCR.Labels).NotTo(HaveKey("rbac.authorization.k8s.io/aggregate-to-edit"))

		// Admin gets registries + connections:use (two-verb SSAR)
		Expect(adminCR.Rules).To(HaveLen(2))
		Expect(adminCR.Rules[0].Resources).To(ConsistOf("registries"))
		Expect(adminCR.Rules[1].Resources).To(ConsistOf("connections"))
		Expect(adminCR.Rules[1].Verbs).To(ConsistOf("use"))

		// Cleanup admin
		Expect(feast.deleteDataRegistryClusterRoles()).To(Succeed())
		err := k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryClusterRoleName("admin")}, &rbacv1.ClusterRole{})
		Expect(apierrors.IsNotFound(err)).To(BeTrue())
	})

	It("creates and cleans up the full resource set via deployDataRegistry", func() {
		drKey := types.NamespacedName{
			Name:      GetFeastName(featureStore) + "-" + string(DataRegistryFeastType),
			Namespace: typeNamespacedName.Namespace,
		}
		cmKey := types.NamespacedName{
			Name:      feast.dataRegistryAuthCMName(),
			Namespace: typeNamespacedName.Namespace,
		}

		// Disabled → no resources
		Expect(feast.deployDataRegistry()).To(Succeed())
		Expect(apierrors.IsNotFound(k8sClient.Get(ctx, drKey, &appsv1.Deployment{}))).To(BeTrue())

		// Enable → all resources created
		setAnnotation("true")
		Expect(feast.deployDataRegistry()).To(Succeed())

		// Deployment exists with 2 containers
		deploy := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, drKey, deploy)).To(Succeed())
		Expect(deploy.Spec.Template.Spec.Containers).To(HaveLen(2))
		Expect(deploy.Spec.Template.Spec.Containers[0].Name).To(Equal(DataRegistryContainerName))
		Expect(deploy.Spec.Template.Spec.Containers[1].Name).To(Equal(DataRegistryProxyContainerName))

		// Service exists
		svc := &corev1.Service{}
		Expect(k8sClient.Get(ctx, drKey, svc)).To(Succeed())
		Expect(svc.Spec.Ports[0].TargetPort).To(Equal(intstr.FromInt32(DataRegistryProxyPort)))

		// Auth ConfigMap exists with regex rewrites
		cm := &corev1.ConfigMap{}
		Expect(k8sClient.Get(ctx, cmKey, cm)).To(Succeed())
		Expect(cm.Data).To(HaveKey("auth.yaml"))
		Expect(cm.Data["auth.yaml"]).To(ContainSubstring("rewrites"))

		// ClusterRoles: all three (viewer, editor, admin) exist
		viewerCR := &rbacv1.ClusterRole{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryClusterRoleName("viewer")}, viewerCR)).To(Succeed())
		Expect(viewerCR.Rules).To(HaveLen(1))
		Expect(viewerCR.Rules[0].APIGroups).To(ConsistOf("dataregistry.opendatahub.io"))
		Expect(viewerCR.Rules[0].Verbs).To(ConsistOf("get", "list", "watch"))
		Expect(viewerCR.Labels).To(HaveKeyWithValue("rbac.authorization.k8s.io/aggregate-to-view", "true"))

		editorCR := &rbacv1.ClusterRole{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryClusterRoleName("editor")}, editorCR)).To(Succeed())
		Expect(editorCR.Rules[0].Verbs).To(ContainElements("create", "update", "patch", "delete"))
		Expect(editorCR.Labels).To(HaveKeyWithValue("rbac.authorization.k8s.io/aggregate-to-edit", "true"))

		adminCR := &rbacv1.ClusterRole{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryClusterRoleName("admin")}, adminCR)).To(Succeed())
		Expect(adminCR.Labels).To(HaveKeyWithValue("rbac.authorization.k8s.io/aggregate-to-admin", "true"))
		Expect(adminCR.Labels).NotTo(HaveKey("rbac.authorization.k8s.io/aggregate-to-view"))
		// Admin has both registries and connections:use
		Expect(adminCR.Rules).To(HaveLen(2))
		Expect(adminCR.Rules[1].Resources).To(ConsistOf("connections"))
		Expect(adminCR.Rules[1].Verbs).To(ConsistOf("use"))

		// Auth-delegator ClusterRoleBinding exists
		crbKey := types.NamespacedName{Name: feast.dataRegistryAuthDelegatorCRBName()}
		crb := &rbacv1.ClusterRoleBinding{}
		Expect(k8sClient.Get(ctx, crbKey, crb)).To(Succeed())
		Expect(crb.RoleRef.Name).To(Equal("system:auth-delegator"))
		Expect(crb.Subjects).To(HaveLen(1))
		Expect(crb.Subjects[0].Kind).To(Equal("ServiceAccount"))
		Expect(crb.Subjects[0].Name).To(Equal(feast.initFeastSA().Name))
		Expect(crb.Subjects[0].Namespace).To(Equal(typeNamespacedName.Namespace))

		// Idempotent
		Expect(feast.deployDataRegistry()).To(Succeed())

		// Remove annotation → all resources cleaned up
		setAnnotation("")
		Expect(feast.deployDataRegistry()).To(Succeed())

		err := k8sClient.Get(ctx, drKey, &appsv1.Deployment{})
		deleted := apierrors.IsNotFound(err)
		if !deleted {
			d := &appsv1.Deployment{}
			_ = k8sClient.Get(ctx, drKey, d)
			deleted = d.DeletionTimestamp != nil
		}
		Expect(deleted).To(BeTrue())

		err = k8sClient.Get(ctx, drKey, &corev1.Service{})
		Expect(apierrors.IsNotFound(err) || err == nil).To(BeTrue())

		err = k8sClient.Get(ctx, cmKey, &corev1.ConfigMap{})
		Expect(apierrors.IsNotFound(err) || err == nil).To(BeTrue())

		// Admin ClusterRole cleaned up
		err = k8sClient.Get(ctx, types.NamespacedName{Name: feast.dataRegistryClusterRoleName("admin")}, &rbacv1.ClusterRole{})
		Expect(apierrors.IsNotFound(err) || err == nil).To(BeTrue())

		// Auth-delegator CRB cleaned up
		err = k8sClient.Get(ctx, crbKey, &rbacv1.ClusterRoleBinding{})
		Expect(apierrors.IsNotFound(err) || err == nil).To(BeTrue())
	})
})
