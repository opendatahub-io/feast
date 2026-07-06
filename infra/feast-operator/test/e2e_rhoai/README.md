# Feast E2E Upgrade Tests

End-to-end tests for validating Feast operator upgrades.

## Overview

These tests validate two distinct upgrade scenarios:

1. **FeatureStore Upgrade Tests** - Verify that user-created Feast FeatureStore CRs survive operator upgrades
2. **Modular Operator Upgrade Tests** - Verify the migration from standalone to modular operator architecture

## Test Scenarios

### Scenario 1: FeatureStore Upgrade Validation

Tests that user workloads (FeatureStore CRs) are preserved during operator upgrades.

#### Pre-Upgrade Test (`feast_preupgrade_test.go`)

**Purpose**: Create FeatureStore CRs and verify they are functional before upgrade

**What it does**:
- Creates FeatureStore CR with S3 registry configuration
- Applies feature definitions (`feast apply`)
- Materializes features to online store
- Validates online feature serving works
- Verifies model execution with features

**Test CR**: `test-s3` FeatureStore in namespace `test-ns-feast-upgrade`

#### Post-Upgrade Test (`feast_postupgrade_test.go`)

**Purpose**: Verify FeatureStore CRs still exist and function after operator upgrade

**What it validates**:
1. **FeatureStore CR survival** - CR still exists with same spec
2. **Deployment availability** - Feast instance deployment is running
3. **Registry integrity** - S3 registry objects are intact
4. **Feature definitions** - `feast apply` still works
5. **Materialization** - Feature materialization intervals preserved
6. **Online serving** - Features are queryable post-upgrade
7. **Model execution** - Model can still retrieve features

**Expected outcome**: User workloads unaffected by operator upgrade

---

### Scenario 2: Modular Operator Upgrade Validation

Tests the migration from standalone feast-operator to modular architecture (feast-module-operator + feast-operator-controller-manager).

**What it validates**:

**Operator Deployments**:
1. `opendatahub-feast-operator` (module operator) - Running
2. `feast-operator-controller-manager` (feast operator) - Running

**CRD Validation**:
3. `feastoperators.components.platform.opendatahub.io` - Exists
4. `featurestores.feast.dev` - Exists

**FeastOperator CR Status**:
5. `default-feastoperator` CR - Ready=True
6. Status.releases - Contains Feast + platform versions

**Module Operator Configuration**:
7. Environment variables - Module configuration injected correctly
8. Deployment selector - Migrated to `app.kubernetes.io/name=feast-operator`

**Zero-Downtime Upgrade**:
9. Feast operator pods - Not restarted during upgrade
10. Spec integrity - FeastOperator CR spec unchanged (requires baseline)
11. No unexpected restarts - Controller pod restart count within limits (requires baseline)

**Platform Integration**:
12. DSC ModulesReady condition - True
13. Platform version handshake - ConfigMap matches CR status

**Expected outcome**: Modular architecture deployed, operator functional, no downtime

## Prerequisites

### Cluster Access
- OpenShift cluster with RHOAI or OpenDataHub installed
- `kubectl` or `oc` CLI configured and authenticated
- Cluster admin permissions

### Required Components
- DSCInitialization created
- DataScienceCluster with FeastOperator managed

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APPLICATIONS_NAMESPACE` | `redhat-ods-applications` | Namespace where feast operators are deployed |

**For upstream ODH**:
```bash
export APPLICATIONS_NAMESPACE=opendatahub
```

**For RHOAI** (default):
```bash
# No export needed - uses default
```

### Run Specific Tests

```bash
# FeatureStore upgrade tests only
go test ./test/e2e_rhoai -run "Feast.*Upgrade" -v

# Modular operator validation only  
go test ./test/e2e_rhoai -run "feastModuleOperatorTest" -v

# All Feast-related tests
go test ./test/e2e_rhoai -v
```


## Development

### Adding New Validations

1. Create validation function in `utils/util.go`:
```go
func ValidateNewCheck(namespace, resourceName string) {
    cmd := exec.Command("kubectl", "get", "resource", resourceName, "-n", namespace)
    out, err := testutils.Run(cmd, "")
    Expect(err).NotTo(HaveOccurred())
    // Add assertions
}
```

2. Add to test flow in `feast_modular_upgrade_test.go`:
```go
By("Description of what you're validating")
ValidateNewCheck(applicationsNamespace, "resource-name")
```

### Test Organization

```
test/e2e_rhoai/
├── README.md                        # This file
├── e2e_suite_test.go               # Test suite setup (Ginkgo)
│
├── feast_preupgrade_test.go        # Scenario 1: Create FeatureStore CRs before upgrade
├── feast_postupgrade_test.go       # Scenario 1: Verify FeatureStore CRs after upgrade
│
├── feast_modular_upgrade_test.go   # Scenario 2: Modular operator deployment validation
│
├── feast_oidc_test.go              # OIDC integration tests
├── feast_wb_connection_integration_test.go  # Workbench connection tests
├── feast_wb_milvus_test.go         # Milvus integration tests
├── feast_wb_ray_offline_store_test.go       # Ray offline store tests
│
├── resources/                       # Test fixtures and manifests
└── utils/
    └── util.go                      # Validation helper functions
```


## Contributing

When adding new tests:

1. ✅ Add descriptive `By()` statements
2. ✅ Use `Expect()` assertions with clear error messages
3. ✅ Add print statements for successful validations (`fmt.Printf("✅ ...")`)
4. ✅ Document what TC-* requirements the test covers
5. ✅ Update this README with new validations

## References

- [OpenDataHub Operator](https://github.com/opendatahub-io/opendatahub-operator)
- [Feast Module Operator](https://github.com/opendatahub-io/feast-module-operator)
- [Feast Operator](https://github.com/feast-dev/feast)
- [Ginkgo Testing Framework](https://onsi.github.io/ginkgo/)

## License

Apache License 2.0 - See LICENSE file in the repository root.
