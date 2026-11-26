package utils

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fakeKubectlScript = `#!/bin/sh
printf '%s\n' "$*" >> "$KUBECTL_LOG"
case "$*" in
"get featurestores.feast.dev simple-feast-remote-setup -n test-ns-remote-registry -o json")
	cat <<'JSON'
{"apiVersion":"feast.dev/v1","kind":"FeatureStore","metadata":{"name":"simple-feast-remote-setup","namespace":"test-ns-remote-registry"},"spec":{"services":{"registry":{"remote":{"feastRef":{"name":"simple-feast-setup","namespace":"test-ns-feast"}}}}}}
JSON
	;;
"get pods -n test-ns-remote-registry -o json")
	printf '%s\n' '{"apiVersion":"v1","kind":"PodList","items":[]}'
	;;
"get pods -n test-ns-feast -o json")
	printf '%s\n' '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"registry-0","namespace":"test-ns-feast"},"spec":{},"status":{}}]}'
	;;
esac
`

func TestDumpDeploymentDiagnosticsIncludesRemoteRegistryNamespace(t *testing.T) {
	binDir := t.TempDir()
	scriptPath := filepath.Join(binDir, "kubectl")
	if err := os.WriteFile(scriptPath, []byte(fakeKubectlScript), 0o755); err != nil {
		t.Fatalf("write fake kubectl: %v", err)
	}

	logPath := filepath.Join(t.TempDir(), "kubectl.log")
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("KUBECTL_LOG", logPath)

	output := captureStdout(t, func() {
		dumpDeploymentDiagnostics("test-ns-remote-registry", "simple-feast-remote-setup", "feast-simple-feast-remote-setup")
	})

	if !strings.Contains(output, "remote registry namespace test-ns-feast") {
		t.Fatalf("remote registry namespace was not dumped:\n%s", output)
	}

	invocations, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("read fake kubectl log: %v", err)
	}

	for _, want := range []string{
		"get pods -n test-ns-feast -o wide",
		"describe pod registry-0 -n test-ns-feast",
		"logs registry-0 -n test-ns-feast --all-containers --tail=100 --ignore-errors --prefix",
		"get networkpolicies -n test-ns-feast -o yaml",
		"get events -n test-ns-feast --sort-by=.lastTimestamp",
	} {
		if !strings.Contains(string(invocations), want+"\n") {
			t.Fatalf("missing kubectl invocation %q in:\n%s", want, invocations)
		}
	}
}

func captureStdout(t *testing.T, fn func()) string {
	t.Helper()

	original := os.Stdout
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatalf("create stdout pipe: %v", err)
	}
	os.Stdout = writer
	defer func() {
		os.Stdout = original
	}()

	fn()

	if err := writer.Close(); err != nil {
		t.Fatalf("close stdout pipe: %v", err)
	}
	out, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read stdout pipe: %v", err)
	}
	return string(out)
}
