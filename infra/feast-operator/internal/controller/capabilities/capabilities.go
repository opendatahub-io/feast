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
	"fmt"
	"os"
	"strings"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const (
	// ConfigMapName is written by feast-module-operator into the operand namespace.
	ConfigMapName = "feast-capabilities-config"

	KeyFeatureStoreEnabled = "featureStoreEnabled"
	KeyDataRegistryEnabled = "dataRegistryEnabled"

	podNamespaceEnvVar = "POD_NAMESPACE"
)

// Config holds platform capability toggles projected by feast-module-operator.
type Config struct {
	FeatureStoreEnabled bool
	DataRegistryEnabled bool
}

// OperatorNamespace returns the namespace where the manager pod runs (operand install NS).
func OperatorNamespace() string {
	return os.Getenv(podNamespaceEnvVar)
}

// Load reads feast-capabilities-config from the operator namespace.
// If the ConfigMap or POD_NAMESPACE is missing, both capabilities default to enabled
// so standalone make deploy and dev clusters keep working.
func Load(ctx context.Context, c client.Client) (Config, error) {
	defaults := Config{
		FeatureStoreEnabled: true,
		DataRegistryEnabled: true,
	}

	ns := OperatorNamespace()
	if ns == "" {
		return defaults, nil
	}

	cm := &corev1.ConfigMap{}
	err := c.Get(ctx, types.NamespacedName{Name: ConfigMapName, Namespace: ns}, cm)
	if apierrors.IsNotFound(err) {
		return defaults, nil
	}
	if err != nil {
		return defaults, fmt.Errorf("read %s/%s: %w", ns, ConfigMapName, err)
	}

	out := defaults
	if v, ok := cm.Data[KeyFeatureStoreEnabled]; ok {
		parsed, parseErr := parseBoolData(v)
		if parseErr != nil {
			return defaults, fmt.Errorf("%s: %w", KeyFeatureStoreEnabled, parseErr)
		}
		out.FeatureStoreEnabled = parsed
	}
	if v, ok := cm.Data[KeyDataRegistryEnabled]; ok {
		parsed, parseErr := parseBoolData(v)
		if parseErr != nil {
			return defaults, fmt.Errorf("%s: %w", KeyDataRegistryEnabled, parseErr)
		}
		out.DataRegistryEnabled = parsed
	}
	return out, nil
}

func parseBoolData(value string) (bool, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "true":
		return true, nil
	case "false":
		return false, nil
	default:
		return false, fmt.Errorf("invalid boolean %q (want true or false)", value)
	}
}
