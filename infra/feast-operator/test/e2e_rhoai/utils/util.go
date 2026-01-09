package utils

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"

	testutils "github.com/feast-dev/feast/infra/feast-operator/test/utils"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

const (
	FeastPrefix = "feast-"
)

func ListConfigMaps(namespace string) ([]string, error) {
	cmd := exec.Command("kubectl", "get", "cm", "-n", namespace, "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}")
	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("failed to list config maps in namespace %s. Error: %v. Stderr: %s",
			namespace, err, stderr.String())
	}

	configMaps := strings.Split(strings.TrimSpace(out.String()), "\n")
	// Filter out empty strings
	var result []string
	for _, cm := range configMaps {
		if cm != "" {
			result = append(result, cm)
		}
	}
	return result, nil
}

// VerifyConfigMapExistsInList checks if a ConfigMap exists in the list of ConfigMaps
func VerifyConfigMapExistsInList(namespace, configMapName string) (bool, error) {
	configMaps, err := ListConfigMaps(namespace)
	if err != nil {
		return false, err
	}

	for _, cm := range configMaps {
		if cm == configMapName {
			return true, nil
		}
	}

	return false, nil
}

// checkIfConfigMapExists validates if a config map exists using the kubectl CLI.
func checkIfConfigMapExists(namespace, configMapName string) error {
	cmd := exec.Command("kubectl", "get", "cm", configMapName, "-n", namespace)

	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to find config map %s in namespace %s. Error: %v. Stderr: %s",
			configMapName, namespace, err, stderr.String())
	}

	// Check the output to confirm presence
	if !strings.Contains(out.String(), configMapName) {
		return fmt.Errorf("config map %s not found in namespace %s", configMapName, namespace)
	}

	return nil
}

// VerifyFeastConfigMapExists verifies that a ConfigMap exists and contains the specified key/file
func VerifyFeastConfigMapExists(namespace, configMapName, expectedKey string) error {
	// First verify the ConfigMap exists
	if err := checkIfConfigMapExists(namespace, configMapName); err != nil {
		return fmt.Errorf("config map %s does not exist: %w", configMapName, err)
	}

	// Get the ConfigMap data to verify the key exists
	cmd := exec.Command("kubectl", "get", "cm", configMapName, "-n", namespace, "-o", "jsonpath={.data."+expectedKey+"}")
	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to get config map data for %s in namespace %s. Error: %v. Stderr: %s",
			configMapName, namespace, err, stderr.String())
	}

	configContent := out.String()
	if configContent == "" {
		return fmt.Errorf("config map %s does not contain key %s", configMapName, expectedKey)
	}

	return nil
}

// VerifyFeastConfigMapContent verifies that a ConfigMap contains the expected feast configuration content
// This assumes the ConfigMap and key already exist (use VerifyFeastConfigMapExists first)
func VerifyFeastConfigMapContent(namespace, configMapName, expectedKey string, expectedContent []string) error {
	// Get the ConfigMap data
	cmd := exec.Command("kubectl", "get", "cm", configMapName, "-n", namespace, "-o", "jsonpath={.data."+expectedKey+"}")
	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to get config map data for %s in namespace %s. Error: %v. Stderr: %s",
			configMapName, namespace, err, stderr.String())
	}

	configContent := out.String()
	if configContent == "" {
		return fmt.Errorf("config map %s does not contain key %s", configMapName, expectedKey)
	}

	// Verify all expected content strings are present
	for _, expected := range expectedContent {
		if !strings.Contains(configContent, expected) {
			return fmt.Errorf("config map %s content does not contain expected string: %s. Content:\n%s",
				configMapName, expected, configContent)
		}
	}

	return nil
}

func ApplyFeastPermissions(fileName string, registryFilePath string, namespace string, podNamePrefix string) {
	By("Applying Feast permissions to the Feast registry pod")

	// 1. Get the pod by prefix
	By(fmt.Sprintf("Finding pod with prefix %q in namespace %q", podNamePrefix, namespace))
	pod, err := getPodByPrefix(namespace, podNamePrefix)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	ExpectWithOffset(1, pod).NotTo(BeNil())

	podName := pod.Name
	fmt.Printf("Found pod: %s\n", podName)

	cmd := exec.Command(
		"oc", "cp",
		fileName, // local source file
		fmt.Sprintf("%s/%s:%s", namespace, podName, registryFilePath), // remote destination
		"-c", "registry",
	)

	_, err = testutils.Run(cmd, "/test/e2e_rhoai")
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	fmt.Printf("Successfully copied file to pod: %s\n", podName)

	// Run `feast apply` inside the pod to apply updated permissions
	By("Running feast apply inside the Feast registry pod")
	cmd = exec.Command(
		"oc", "exec", podName,
		"-n", namespace,
		"-c", "registry",
		"--",
		"bash", "-c",
		"cd /feast-data/credit_scoring_local/feature_repo && feast apply",
	)
	_, err = testutils.Run(cmd, "/test/e2e_rhoai")
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	fmt.Println("Feast permissions apply executed successfully")

	By("Validating that Feast permission has been applied")

	cmd = exec.Command(
		"oc", "exec", podName,
		"-n", namespace,
		"-c", "registry",
		"--",
		"feast", "permissions", "list",
	)

	output, err := testutils.Run(cmd, "/test/e2e_rhoai")
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	// Change "feast-auth" if your permission name is different
	ExpectWithOffset(1, output).To(ContainSubstring("feast-auth"), "Expected permission 'feast-auth' to exist")

	fmt.Println("Verified: Feast permission 'feast-auth' exists")
}

// CreateNamespace - create the namespace for tests
func CreateNamespace(namespace string, testDir string) error {
	cmd := exec.Command("kubectl", "create", "ns", namespace)
	output, err := testutils.Run(cmd, testDir)
	if err != nil {
		return fmt.Errorf("failed to create namespace %s: %v\nOutput: %s", namespace, err, output)
	}
	return nil
}

// DeleteNamespace - Delete the namespace for tests
func DeleteNamespace(namespace string, testDir string) error {
	cmd := exec.Command("kubectl", "delete", "ns", namespace, "--timeout=180s")
	output, err := testutils.Run(cmd, testDir)
	if err != nil {
		return fmt.Errorf("failed to delete namespace %s: %v\nOutput: %s", namespace, err, output)
	}
	return nil
}

// applies the manifests for Redis and Postgres and checks whether the deployments become available
func ApplyFeastInfraManifestsAndVerify(namespace string, testDir string) {
	By("Applying postgres.yaml and redis.yaml manifests")
	cmd := exec.Command("kubectl", "apply", "-n", namespace, "-f", "test/testdata/feast_integration_test_crs/postgres.yaml", "-f", "test/testdata/feast_integration_test_crs/redis.yaml")
	_, cmdOutputerr := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, cmdOutputerr).NotTo(HaveOccurred())
	CheckDeployment(namespace, "postgres")
	CheckDeployment(namespace, "redis")
}

// CheckDeployment verifies the specified deployment exists and is in the "Available" state.
func CheckDeployment(namespace, name string) {
	By(fmt.Sprintf("Waiting for %s deployment to become available", name))
	err := testutils.CheckIfDeploymentExistsAndAvailable(namespace, name, 2*testutils.Timeout)
	Expect(err).ToNot(HaveOccurred(), fmt.Sprintf(
		"Deployment %s is not available but expected to be.\nError: %v", name, err,
	))
	fmt.Printf("Deployment %s is available\n", name)
}

// validate that the status of the FeatureStore CR is "Ready".
func ValidateFeatureStoreCRStatus(namespace, crName string) {
	Eventually(func() string {
		cmd := exec.Command("kubectl", "get", "feast", crName, "-n", namespace, "-o", "jsonpath={.status.phase}")
		output, err := cmd.Output()
		if err != nil {
			return ""
		}
		return string(output)
	}, "2m", "5s").Should(Equal("Ready"), "Feature Store CR did not reach 'Ready' state in time")

	fmt.Printf("✅ Feature Store CR %s/%s is in Ready state\n", namespace, crName)
}

// validates the `feast apply` and `feast materialize-incremental commands were configured in the FeatureStore CR's CronJob config.
func VerifyApplyFeatureStoreDefinitions(namespace string, feastCRName string, feastDeploymentName string) {
	By("Verify CronJob commands in FeatureStore CR")
	cmd := exec.Command("kubectl", "get", "-n", namespace, "feast/"+feastCRName, "-o", "jsonpath={.status.applied.cronJob.containerConfigs.commands}")
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("Failed to fetch CronJob commands:\n%s", output))
	commands := string(output)
	fmt.Println("CronJob commands:", commands)
	Expect(commands).To(ContainSubstring(`feast apply`))
	Expect(commands).To(ContainSubstring(`feast materialize-incremental $(date -u +'%Y-%m-%dT%H:%M:%S')`))

	CreateAndVerifyJobFromCron(namespace, feastDeploymentName, "feast-test-apply", "", []string{
		"No project found in the repository",
		"Applying changes for project credit_scoring_local",
		"Deploying infrastructure for credit_history",
		"Deploying infrastructure for zipcode_features",
		"Materializing 2 feature views to",
		"into the redis online store",
		"credit_history from",
		"zipcode_features from",
	})
}

// Create a Job and verifies its logs contain expected substrings
func CreateAndVerifyJobFromCron(namespace, cronName, jobName, testDir string, expectedLogSubstrings []string) {
	By(fmt.Sprintf("Creating Job %s from CronJob %s", jobName, cronName))
	cmd := exec.Command("kubectl", "create", "job", "--from=cronjob/"+cronName, jobName, "-n", namespace)
	_, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	By("Waiting for Job completion")
	cmd = exec.Command("kubectl", "wait", "--for=condition=complete", "--timeout=5m", "job/"+jobName, "-n", namespace)
	_, err = testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	By("Checking logs of completed job")
	cmd = exec.Command("kubectl", "logs", "job/"+jobName, "-n", namespace, "--all-containers=true")
	output, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	outputStr := string(output)
	ansi := regexp.MustCompile(`\x1b\[[0-9;]*m`)
	outputStr = ansi.ReplaceAllString(outputStr, "")
	for _, expected := range expectedLogSubstrings {
		Expect(outputStr).To(ContainSubstring(expected))
	}
	fmt.Printf("created Job %s and Verified expected Logs ", jobName)
}

// validate the feature store yaml
func validateFeatureStoreYaml(namespace, deployment string) {
	cmd := exec.Command("kubectl", "exec", "deploy/"+deployment, "-n", namespace, "-c", "online", "--", "cat", "feature_store.yaml")
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), "Failed to read feature_store.yaml")

	content := string(output)
	Expect(content).To(ContainSubstring("offline_store:\n    type: duckdb"))
	Expect(content).To(ContainSubstring("online_store:\n    type: redis"))
	Expect(content).To(ContainSubstring("registry_type: sql"))
}

// apply and verifies the Feast deployment becomes available, the CR status is "Ready
func ApplyFeastYamlAndVerify(namespace string, testDir string, feastDeploymentName string, feastCRName string, feastYAMLFilePath string) {
	By("Applying Feast yaml for secrets and Feature store CR")
	cmd := exec.Command("kubectl", "apply", "-n", namespace,
		"-f", feastYAMLFilePath)
	_, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	CheckDeployment(namespace, feastDeploymentName)

	By("Verify Feature Store CR is in Ready state")
	ValidateFeatureStoreCRStatus(namespace, feastCRName)

	By("Verifying that the Postgres DB contains the expected Feast tables")
	cmd = exec.Command("kubectl", "exec", "deploy/postgres", "-n", namespace, "--", "psql", "-h", "localhost", "-U", "feast", "feast", "-c", `\dt`)
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("Failed to get tables from Postgres. Output:\n%s", output))
	outputStr := string(output)
	fmt.Println("Postgres Tables:\n", outputStr)
	// List of expected tables
	expectedTables := []string{
		"data_sources", "entities", "feast_metadata", "feature_services", "feature_views",
		"managed_infra", "on_demand_feature_views", "permissions", "projects",
		"saved_datasets", "stream_feature_views", "validation_references",
	}
	for _, table := range expectedTables {
		Expect(outputStr).To(ContainSubstring(table), fmt.Sprintf("Expected table %q not found in output:\n%s", table, outputStr))
	}

	By("Verifying that the Feast repo was successfully cloned by the init container")
	cmd = exec.Command("kubectl", "logs", "-f", "-n", namespace, "deploy/"+feastDeploymentName, "-c", "feast-init")
	output, err = cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("Failed to get logs from init container. Output:\n%s", output))
	outputStr = string(output)
	fmt.Println("Init Container Logs:\n", outputStr)
	// Assert that the logs contain success indicators
	Expect(outputStr).To(ContainSubstring("Feast repo creation complete"), "Expected Feast repo creation message not found")

	By("Verifying client feature_store.yaml for expected store types")
	validateFeatureStoreYaml(namespace, feastDeploymentName)
}

// checks for the presence of expected entities, features, feature views, data sources, etc.
func VerifyFeastMethods(namespace string, feastDeploymentName string, testDir string) {
	type feastCheck struct {
		command   []string
		expected  []string
		logPrefix string
	}
	checks := []feastCheck{
		{
			command:   []string{"feast", "projects", "list"},
			expected:  []string{"credit_scoring_local"},
			logPrefix: "Projects List",
		},
		{
			command:   []string{"feast", "feature-views", "list"},
			expected:  []string{"credit_history", "zipcode_features", "total_debt_calc"},
			logPrefix: "Feature Views List",
		},
		{
			command:   []string{"feast", "entities", "list"},
			expected:  []string{"zipcode", "dob_ssn"},
			logPrefix: "Entities List",
		},
		{
			command:   []string{"feast", "data-sources", "list"},
			expected:  []string{"Zipcode source", "Credit history", "application_data"},
			logPrefix: "Data Sources List",
		},
		{
			command: []string{"feast", "features", "list"},
			expected: []string{
				"credit_card_due", "mortgage_due", "student_loan_due", "vehicle_loan_due",
				"hard_pulls", "missed_payments_2y", "missed_payments_1y", "missed_payments_6m",
				"bankruptcies", "city", "state", "location_type", "tax_returns_filed",
				"population", "total_wages", "total_debt_due",
			},
			logPrefix: "Features List",
		},
	}

	for _, check := range checks {
		cmd := exec.Command("kubectl", "exec", "deploy/"+feastDeploymentName, "-n", namespace, "-c", "online", "--")
		cmd.Args = append(cmd.Args, check.command...)
		output, err := testutils.Run(cmd, testDir)
		ExpectWithOffset(1, err).NotTo(HaveOccurred())

		fmt.Printf("%s:\n%s\n", check.logPrefix, string(output))
		VerifyOutputContains(output, check.expected)
	}
}

// ReplaceNamespaceInYaml reads a YAML file, replaces all existingNamespace with the actual namespace
func ReplaceNamespaceInYamlFilesInPlace(filePaths []string, existingNamespace string, actualNamespace string) error {
	for _, filePath := range filePaths {
		data, err := os.ReadFile(filePath)
		if err != nil {
			return fmt.Errorf("failed to read YAML file %s: %w", filePath, err)
		}
		updated := strings.ReplaceAll(string(data), existingNamespace, actualNamespace)

		err = os.WriteFile(filePath, []byte(updated), 0644)
		if err != nil {
			return fmt.Errorf("failed to write updated YAML file %s: %w", filePath, err)
		}
	}
	return nil
}

// asserts that all expected substrings are present in the given output.
func VerifyOutputContains(output []byte, expectedSubstrings []string) {
	outputStr := string(output)
	for _, expected := range expectedSubstrings {
		Expect(outputStr).To(ContainSubstring(expected), fmt.Sprintf("Expected output to contain: %s", expected))
	}
}
