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

package capabilities

import (
	"context"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
)

func TestLoadDefaultsWhenConfigMapMissing(t *testing.T) {
	t.Setenv(podNamespaceEnvVar, "feast-operator-system")

	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	_ = feastdevv1.AddToScheme(scheme)
	cl := fake.NewClientBuilder().WithScheme(scheme).Build()

	cfg, err := Load(context.Background(), cl)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.FeatureStoreEnabled || !cfg.DataRegistryEnabled {
		t.Fatalf("expected both enabled, got %+v", cfg)
	}
}

func TestLoadFromConfigMap(t *testing.T) {
	t.Setenv(podNamespaceEnvVar, "redhat-ods-applications")

	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      ConfigMapName,
			Namespace: "redhat-ods-applications",
		},
		Data: map[string]string{
			KeyFeatureStoreEnabled: "false",
			KeyDataRegistryEnabled: "true",
		},
	}
	cl := fake.NewClientBuilder().WithScheme(scheme).WithObjects(cm).Build()

	cfg, err := Load(context.Background(), cl)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.FeatureStoreEnabled {
		t.Fatal("expected feature store disabled")
	}
	if !cfg.DataRegistryEnabled {
		t.Fatal("expected data registry enabled")
	}
}
