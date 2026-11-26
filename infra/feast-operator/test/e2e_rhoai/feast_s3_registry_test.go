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
	. "github.com/onsi/gomega"
)

// Standalone S3 registry FeatureStore e2e (ported from feast-dev/feast#7173).
// Runs apply + materialize against S3/DynamoDB; skips when AWS env vars are unset.
var _ = Describe("Feast S3 Registry E2E Testing", Ordered, func() {
	const (
		namespace           = "test-ns-feast-s3"
		testDir             = "/test/e2e_rhoai"
		feastCRName         = "test-s3"
		feastDeploymentName = FeastPrefix + "test-s3"
		feastS3YamlPath     = "test/e2e_rhoai/resources/feast_s3_e2e.yaml"
		s3RegistryTest      = "TestS3RegistryFeatureStore"
	)

	BeforeAll(func() {
		By(fmt.Sprintf("Creating test namespace: %s", namespace))
		Expect(CreateNamespace(namespace, testDir)).To(Succeed())
		fmt.Printf("Namespace %s created successfully\n", namespace)
	})

	AfterAll(func() {
		By(fmt.Sprintf("Deleting test namespace: %s", namespace))
		Expect(DeleteNamespace(namespace, testDir)).To(Succeed())
		fmt.Printf("Namespace %s deleted successfully\n", namespace)
	})

	runS3RegistryTest := func() {
		SkipIfMissingFeastS3Env()

		By("Applying Feast S3 manifest and verifying CR Ready / feature_store.yaml")
		ApplyFeastS3YamlAndVerify(namespace, testDir, feastS3YamlPath, feastCRName)

		By("Running feast apply and feast materialize-incremental via CronJob")
		VerifyApplyFeatureStoreDefinitionsS3Job(namespace, feastCRName, feastDeploymentName, "feast-test-apply-s3-e2e")

		By("Validating Feast project list for driver_ranking")
		VerifyFeastMethodsForDriverRanking(namespace, feastDeploymentName, testDir)
	}

	Context("Feast S3 Registry Test", func() {
		It("Should deploy and run a "+s3RegistryTest+" with S3-backed registry apply/materialize successfully", runS3RegistryTest)
	})
})
