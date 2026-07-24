/*
Copyright 2025 Feast Community.

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

package e2erhoai

import (
	"fmt"

	. "github.com/feast-dev/feast/infra/feast-operator/test/e2e_rhoai/utils"
	. "github.com/onsi/ginkgo/v2"
)

var _ = Describe("Feast Modular Upgrade scenario Testing", Ordered, func() {
	const (
		testDir                     = "/test/e2e_rhoai"
		feastOperatorDeploymentName = "opendatahub-feast-operator"
	)

	var applicationsNamespace string

	BeforeAll(func() {
		applicationsNamespace = GetApplicationsNamespace()
		By(fmt.Sprintf("Using applications namespace: %s", applicationsNamespace))
	})

	runFeastModuleOperatorTest := func() {
		By("Verifying feast-module-operator Deployment survived upgrade")
		CheckDeployment(applicationsNamespace, "opendatahub-feast-operator")

		By("Verifying feast-operator-controller-manager has app.kubernetes.io/name=feast-operator selector label")
		ValidateDeploymentSelector(
			applicationsNamespace,
			"feast-operator-controller-manager",
			map[string]string{"app.kubernetes.io/name": "feast-operator"},
			testDir,
		)

		By("Verify CRD feastoperator crd exists post-upgrade")
		ValidateCRDExists("feastoperators.components.platform.opendatahub.io", testDir)

		By("validate CR status for default-feastoperator")
		ValidateFeastOperatorCRStatus(applicationsNamespace, "default-feastoperator", testDir)

		By("Verifying featurestore  CRD exists post-upgrade")
		ValidateCRDExists("featurestores.feast.dev", testDir)

		By("Validating feast-module-operator environment variable injection into manager container")
		ValidateFeastModuleOperatorEnvVars(applicationsNamespace, testDir)

		By("Verifying feast-operator pods were NOT restarted and deployment selector was migrated")
		ValidateFeastOperandPodsNotRestarted(applicationsNamespace, testDir)

		By("Checking if pre-upgrade baseline ConfigMap exists")
		baselineExists := CheckConfigMapExists(applicationsNamespace, "upgrade-baseline-cm")

		if baselineExists {
			By("Retrieving pre-upgrade baseline ConfigMap for comparison")
			baselineData := GetConfigMap(applicationsNamespace, "upgrade-baseline-cm", applicationsNamespace)

			By("Verifying FeastOperator spec is intact vs pre-upgrade baseline")
			VerifyFeastOperatorSpecIntegrity(applicationsNamespace, "default-feastoperator", baselineData)

			By("Verifying no unexpected pod restarts on feast-operator-controller-manager after upgrade")
			ValidateNoPodRestarts(applicationsNamespace, "feast-operator-controller-manager", baselineData)
		}

		By("Verifying DSC ModulesReady condition is True post-upgrade")
		ValidateDSCCondition("ModulesReady", "True")

		By("Verifying platform version handshake reflects upgraded version")
		ValidatePlatformVersionHandshake(applicationsNamespace)

		By("Verifying FeastOperator status.releases is updated after upgrade")
		ValidateFeastOperatorReleases(applicationsNamespace, "default-feastoperator")

		fmt.Println("feast-module-operator modular upgrade checks passed")

	}

	// This context verifies that a pre-created Feast FeatureStore CR continues to function as expected
	// after an upgrade. It validates `feast apply`, registry sync, feature retrieval, and model execution.
	Context("Feast post Upgrade Test", func() {
		It("Should run a feastModuleOperatorTest scenario to verify feast-module-operator, FeastOperator CR, feast-operator-controller-manager survived upgrade", runFeastModuleOperatorTest)
	})
})
