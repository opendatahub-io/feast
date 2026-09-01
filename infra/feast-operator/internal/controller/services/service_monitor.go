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

package services

import (
	"encoding/json"

	feastdevv1 "github.com/feast-dev/feast/infra/feast-operator/api/v1"
	monitoringv1 "github.com/prometheus-operator/prometheus-operator/pkg/apis/monitoring/v1"
	monitoringv1apply "github.com/prometheus-operator/prometheus-operator/pkg/client/applyconfiguration/monitoring/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	metav1apply "k8s.io/client-go/applyconfigurations/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

var serviceMonitorGVK = schema.GroupVersionKind{
	Group:   "monitoring.coreos.com",
	Version: "v1",
	Kind:    "ServiceMonitor",
}

// createOrDeleteServiceMonitor reconciles the ServiceMonitor for the
// FeatureStore's online store metrics endpoint using Server-Side Apply.
// When the Prometheus Operator CRD is not present in the cluster, this is
// a no-op. When metrics are enabled on the online store, a ServiceMonitor
// is applied; otherwise any existing ServiceMonitor is deleted.
func (feast *FeastServices) createOrDeleteServiceMonitor() error {
	if !hasServiceMonitorCRD {
		return nil
	}

	if feast.isOnlineStore() && feast.isMetricsEnabled(OnlineFeastType) {
		return feast.applyServiceMonitor()
	}

	return feast.deleteServiceMonitor()
}

func (feast *FeastServices) applyServiceMonitor() error {
	smApply := feast.buildServiceMonitorApplyConfig()
	data, err := json.Marshal(smApply)
	if err != nil {
		return err
	}

	sm := feast.initServiceMonitor()
	logger := log.FromContext(feast.Handler.Context)
	if err := feast.Handler.Client.Patch(feast.Handler.Context, sm,
		client.RawPatch(types.ApplyPatchType, data),
		client.FieldOwner(fieldManager), client.ForceOwnership); err != nil {
		return err
	}
	logger.Info("Successfully applied", "ServiceMonitor", sm.GetName())

	return nil
}

func (feast *FeastServices) deleteServiceMonitor() error {
	sm := feast.initServiceMonitor()
	return feast.Handler.DeleteOwnedFeastObj(sm)
}

func (feast *FeastServices) initServiceMonitor() *unstructured.Unstructured {
	sm := &unstructured.Unstructured{}
	sm.SetGroupVersionKind(serviceMonitorGVK)
	sm.SetName(feast.GetFeastServiceName(OnlineFeastType))
	sm.SetNamespace(feast.Handler.FeatureStore.Namespace)
	return sm
}

// buildServiceMonitorApplyConfig constructs the fully desired ServiceMonitor
// state for Server-Side Apply.
func (feast *FeastServices) buildServiceMonitorApplyConfig() *monitoringv1apply.ServiceMonitorApplyConfiguration {
	cr := feast.Handler.FeatureStore
	objMeta := feast.GetObjectMetaType(OnlineFeastType)

	return monitoringv1apply.ServiceMonitor(objMeta.Name, objMeta.Namespace).
		WithLabels(feast.getFeastTypeLabels(OnlineFeastType)).
		WithOwnerReferences(
			metav1apply.OwnerReference().
				WithAPIVersion(feastdevv1.GroupVersion.String()).
				WithKind("FeatureStore").
				WithName(cr.Name).
				WithUID(cr.UID).
				WithController(true).
				WithBlockOwnerDeletion(true),
		).
		WithSpec(monitoringv1apply.ServiceMonitorSpec().
			WithEndpoints(
				monitoringv1apply.Endpoint().
					WithPort("metrics").
					WithPath("/metrics"),
			).
			WithSelector(metav1apply.LabelSelector().
				WithMatchLabels(map[string]string{
					NameLabelKey:        cr.Name,
					ServiceTypeLabelKey: string(OnlineFeastType),
				}),
			),
		)
}

// ---------------------------------------------------------------------------
// Data Registry ServiceMonitor
// ---------------------------------------------------------------------------

// createOrDeleteDataRegistryServiceMonitor reconciles the ServiceMonitor for the
// data-registry Prometheus metrics endpoint (:8000/metrics). No-op when the
// Prometheus Operator CRD is absent.
func (feast *FeastServices) createOrDeleteDataRegistryServiceMonitor() error {
	if !hasServiceMonitorCRD {
		return nil
	}
	if feast.isDataRegistryEnabled() {
		return feast.applyDataRegistryServiceMonitor()
	}
	return feast.deleteDataRegistryServiceMonitor()
}

func (feast *FeastServices) applyDataRegistryServiceMonitor() error {
	smApply := feast.buildDataRegistryServiceMonitorApplyConfig()
	data, err := json.Marshal(smApply)
	if err != nil {
		return err
	}

	sm := feast.initDataRegistryServiceMonitor()
	logger := log.FromContext(feast.Handler.Context)
	if err := feast.Handler.Client.Patch(feast.Handler.Context, sm,
		client.RawPatch(types.ApplyPatchType, data),
		client.FieldOwner(fieldManager), client.ForceOwnership); err != nil {
		return err
	}
	logger.Info("Successfully applied", "ServiceMonitor", sm.GetName())
	return nil
}

func (feast *FeastServices) deleteDataRegistryServiceMonitor() error {
	sm := feast.initDataRegistryServiceMonitor()
	return feast.Handler.DeleteOwnedFeastObj(sm)
}

func (feast *FeastServices) initDataRegistryServiceMonitor() *unstructured.Unstructured {
	sm := &unstructured.Unstructured{}
	sm.SetGroupVersionKind(serviceMonitorGVK)
	sm.SetName(feast.GetFeastServiceName(DataRegistryFeastType))
	sm.SetNamespace(feast.Handler.FeatureStore.Namespace)
	return sm
}

// buildDataRegistryServiceMonitorApplyConfig constructs the desired ServiceMonitor
// for the data-registry metrics port with a 30-second scrape interval.
func (feast *FeastServices) buildDataRegistryServiceMonitorApplyConfig() *monitoringv1apply.ServiceMonitorApplyConfiguration {
	cr := feast.Handler.FeatureStore
	objMeta := feast.GetObjectMetaType(DataRegistryFeastType)

	return monitoringv1apply.ServiceMonitor(objMeta.Name, objMeta.Namespace).
		WithLabels(feast.getFeastTypeLabels(DataRegistryFeastType)).
		WithOwnerReferences(
			metav1apply.OwnerReference().
				WithAPIVersion(feastdevv1.GroupVersion.String()).
				WithKind("FeatureStore").
				WithName(cr.Name).
				WithUID(cr.UID).
				WithController(true).
				WithBlockOwnerDeletion(true),
		).
		WithSpec(monitoringv1apply.ServiceMonitorSpec().
			WithEndpoints(
				monitoringv1apply.Endpoint().
					WithPort(metricsPortName).
					WithPath("/metrics").
					WithInterval("30s"),
			).
			WithSelector(metav1apply.LabelSelector().
				WithMatchLabels(map[string]string{
					NameLabelKey:        cr.Name,
					ServiceTypeLabelKey: string(DataRegistryFeastType),
				}),
			),
		)
}

// ---------------------------------------------------------------------------
// Data Registry PrometheusRule
// ---------------------------------------------------------------------------

var prometheusRuleGVK = schema.GroupVersionKind{
	Group:   "monitoring.coreos.com",
	Version: "v1",
	Kind:    "PrometheusRule",
}

// createOrDeleteDataRegistryPrometheusRule reconciles the PrometheusRule for
// data-registry alert definitions. No-op when the Prometheus Operator CRD is
// not present.
func (feast *FeastServices) createOrDeleteDataRegistryPrometheusRule() error {
	if !hasServiceMonitorCRD {
		return nil
	}
	if feast.isDataRegistryEnabled() {
		return feast.applyDataRegistryPrometheusRule()
	}
	return feast.deleteDataRegistryPrometheusRule()
}

func (feast *FeastServices) applyDataRegistryPrometheusRule() error {
	prApply := feast.buildDataRegistryPrometheusRuleApplyConfig()
	data, err := json.Marshal(prApply)
	if err != nil {
		return err
	}

	pr := feast.initDataRegistryPrometheusRule()
	logger := log.FromContext(feast.Handler.Context)
	if err := feast.Handler.Client.Patch(feast.Handler.Context, pr,
		client.RawPatch(types.ApplyPatchType, data),
		client.FieldOwner(fieldManager), client.ForceOwnership); err != nil {
		return err
	}
	logger.Info("Successfully applied", "PrometheusRule", pr.GetName())
	return nil
}

func (feast *FeastServices) deleteDataRegistryPrometheusRule() error {
	pr := feast.initDataRegistryPrometheusRule()
	return feast.Handler.DeleteOwnedFeastObj(pr)
}

func (feast *FeastServices) initDataRegistryPrometheusRule() *unstructured.Unstructured {
	pr := &unstructured.Unstructured{}
	pr.SetGroupVersionKind(prometheusRuleGVK)
	pr.SetName(feast.GetFeastServiceName(DataRegistryFeastType))
	pr.SetNamespace(feast.Handler.FeatureStore.Namespace)
	return pr
}

// buildDataRegistryPrometheusRuleApplyConfig constructs the desired PrometheusRule
// with two alert rules targeting the standard Feast metrics exposed on :8000:
//   - DataCatalogHighErrorRate: fires when 5xx rate exceeds 5% for 5 minutes
//   - DataCatalogSearchLatencyHigh: fires when p95 request latency exceeds 500ms for 10 minutes
func (feast *FeastServices) buildDataRegistryPrometheusRuleApplyConfig() *monitoringv1apply.PrometheusRuleApplyConfiguration {
	cr := feast.Handler.FeatureStore
	objMeta := feast.GetObjectMetaType(DataRegistryFeastType)

	fiveMin := monitoringv1.Duration("5m")
	tenMin := monitoringv1.Duration("10m")

	// Alerts reference the standard feast_feature_server_* metrics that the
	// Feast process already exports — no duplicate datacatalog_* metrics needed.
	highErrorRateAlert := monitoringv1apply.Rule().
		WithAlert("DataCatalogHighErrorRate").
		WithExpr(intstr.FromString(
			`sum(rate(feast_feature_server_request_total{status=~"5.."}[5m]))` +
				` / sum(rate(feast_feature_server_request_total[5m])) > 0.05`,
		)).
		WithFor(fiveMin).
		WithLabels(map[string]string{"severity": "critical"}).
		WithAnnotations(map[string]string{
			"description": "Data Registry 5xx error rate exceeds 5% for 5 minutes",
		})

	searchLatencyAlert := monitoringv1apply.Rule().
		WithAlert("DataCatalogSearchLatencyHigh").
		WithExpr(intstr.FromString(
			`histogram_quantile(0.95,` +
				` sum(rate(feast_feature_server_request_latency_seconds_bucket{endpoint="/search"}[5m]))` +
				` by (le)) > 0.5`,
		)).
		WithFor(tenMin).
		WithLabels(map[string]string{"severity": "warning"}).
		WithAnnotations(map[string]string{
			"description": "95th percentile search latency exceeds 500ms for 10 minutes",
		})

	return monitoringv1apply.PrometheusRule(objMeta.Name, objMeta.Namespace).
		WithLabels(feast.getFeastTypeLabels(DataRegistryFeastType)).
		WithOwnerReferences(
			metav1apply.OwnerReference().
				WithAPIVersion(feastdevv1.GroupVersion.String()).
				WithKind("FeatureStore").
				WithName(cr.Name).
				WithUID(cr.UID).
				WithController(true).
				WithBlockOwnerDeletion(true),
		).
		WithSpec(monitoringv1apply.PrometheusRuleSpec().
			WithGroups(
				monitoringv1apply.RuleGroup().
					WithName("data-registry.rules").
					WithRules(highErrorRateAlert, searchLatencyAlert),
			),
		)
}
