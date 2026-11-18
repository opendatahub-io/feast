# PR Build Validation

This repository runs a Konflux build simulator on pull requests. It mirrors the container builds Konflux runs from `.tekton/` and validates the dependency, overlay, and manifest inputs that fail in Konflux or in downstream hermetic builds.

The workflow is `.github/workflows/pr-build-validation.yml`; the reusable implementation is `scripts/validate-build.sh`.

## Konflux build targets

| Image | PipelineRun | Build context | Dockerfile |
|-------|-------------|---------------|------------|
| `quay.io/opendatahub/feature-server` | `.tekton/odh-feature-server-pull-request.yaml` | `.` | `sdk/python/feast/infra/feature_servers/multicloud/Dockerfile` |
| `quay.io/opendatahub/feast-operator` | `.tekton/odh-feast-operator-pull-request.yaml` | `infra/feast-operator` | `Dockerfile` |
| `quay.io/opendatahub/odh-feast-mcp` | `.tekton/odh-feast-mcp-pull-request.yaml` | `.` | `sdk/python/feast/mcp/docker/Dockerfile` |

All three build for `linux/x86_64`, `linux/arm64`, and `linux/ppc64le`.

## What the workflow validates

### Hermetic Build Preflight

- Resolves the feature-server dependency closure with `uv pip compile` for the Konflux target platform (`x86_64-manylinux_2_34`, Python 3.12). `pip` alone cannot resolve this graph — it fails with `resolution-too-deep`, which is why the image installs with `uv`.
- Populates an isolated `UV_CACHE_DIR` from that resolved lock, deletes the venv, and then runs the image's own command (`uv pip install --offline -r requirements.txt`) against that cache only. That is the Python equivalent of a Hermeto prefetch: a dependency missing from the prefetched set fails here instead of in a hermetic build.
- The install phase runs only on Linux hosts whose architecture matches the lock's target (`x86_64`). Anywhere else it reports a resolution-only run instead of claiming target-platform coverage; the `arm64` and `ppc64le` images are not install-tested here.
- Copies `sdk/python/feast` over the installed package exactly as the Dockerfile does, then imports `feast.feature_server`, `feast.feature_store`, `feast.infra.registry`, and `feast.repo_config`. A syntax error or a missing module in the checkout is caught without waiting for an image build.
- Populates the Go module cache for `infra/feast-operator`, then compiles and vets it for the Konflux target (`linux/amd64`) with `GOPROXY=off` and `CGO_ENABLED=0`, so a host-architecture build cannot stand in for the shipped one.
- Runs `go mod verify` for the operator module.

### Static Validation Checks

- Parses every Konflux pull-request PipelineRun and verifies that its `path-context` / `dockerfile` pair points at a real file, so a renamed or moved Dockerfile fails the PR that renames it.
- Requires every feature-server requirement to be pinned with `==`, and keeps the `feast[...] == X` pin on line 2 because release CI rewrites that line.
- Checks `go.mod` / `go.sum` for both modules, warns about `replace` directives that a prefetch would need to resolve.
- Verifies every `COPY` source in the feature-server Dockerfile exists in the build context, that the image still overlays `sdk/python/feast` from the checkout, and that `.dockerignore` does not exclude it.
- Warns about FIPS release blockers (operator built without `-tags strictfipsruntime`), about `curl | sh` at build time (which a hermetic build cannot run), and about workflow trigger gaps in the existing smoke/e2e workflows.

### Build and Validate the Feature Server Image

- Builds the exact Konflux Dockerfile for `linux/amd64`.
- Starts the image with `infra/scripts/feature_server_docker_smoke.py` and waits for `GET /health`.
- Imports `feast` and `feast.feature_server` inside the running container, proving the checkout overlay is importable in the shipped image.
- Builds the MCP wrapper (`sdk/python/feast/mcp/docker/Dockerfile`) on top of the freshly built feature-server image, with `BASE_IMAGE`/`BASE_TAG` pointed at it, and runs its `feast mcp --help` entrypoint. Konflux builds the MCP image against the published base tag, so this is where a broken wrapper or a missing `feast mcp` entrypoint shows up first.

### Validate Manifests

- Renders `config/default`, `config/overlays/{odh,rhoai,openshift}`, and `config/samples` with kustomize.
- Rejects a Deployment that declares the same env var name twice. The API server keeps only the last value, so the rendered overlay and the effective configuration silently disagree — the mechanism behind RHOAIENG-57579, where `RELATED_IMAGE_CRON_JOB` was duplicated on the controller-manager Deployment.
- Runs kubeconform in strict mode and fails on `statusInvalid` / `statusError` records, so a manifest with an unknown field or a wrong type cannot pass, and on skipped Kubernetes built-in kinds, which catches `Deploymnet`-style typos that `-ignore-missing-schemas` would otherwise hide. The report is requested with `-verbose` because `-output json` omits skipped resources without it.
- No existing workflow builds these overlays, so a broken `resources:` entry currently only fails when the operator is installed.

### Check the operator flag contract

- Extracts the flags the `controller-manager` Deployment passes in the `odh` and `rhoai` overlays.
- Builds `infra/feast-operator/Dockerfile`, runs the resulting binary with `--help`, and fails if the overlay passes a flag the binary does not define.
- This is the mechanism behind RHOAIENG-100779: the overlay passed `--metrics-cert-path`, the operand image did not define it, and the container exited during flag parsing instead of reconciling. Nothing else in either repository compares the rendered manifest against the binary it starts.

## What this does not cover

- **ppc64le.** The feature-server Dockerfile has a Power-specific branch (IBM wheels for `pyarrow`, `re2`, and `grpcio`). GitHub runners cannot build it in reasonable time, so the Power path stays a static check plus the `prebuild-power-base.sh` contract.
- **The feature server through the operator.** `operator-e2e-integration-tests.yml` already deploys the operator to Kind and runs the Ginkgo e2e suite. It is not duplicated here.
- **`DataScienceCluster` reconciliation.** Feast is shipped on the platform by the `data` module, which deploys `opendatahub-io/feast-module-operator`; see the PR description for the handoff.

## Running locally

Prerequisites: Docker, Go 1.26+, `uv`, Python 3.12, `curl`, and `jq`. `manifests` needs kustomize (or `kubectl`) and optionally kubeconform.

```bash
./scripts/validate-build.sh static
./scripts/validate-build.sh hermetic
./scripts/validate-build.sh manifests
./scripts/validate-build.sh operator
./scripts/validate-build.sh image
```

`static` and `manifests` finish in seconds. `manifests` needs `PyYAML`. `hermetic` takes about a minute to resolve the closure and warm the prefetch cache. `operator` and `image` need a Docker daemon; `operator` builds only the operator image, `image` builds the full feature-server image plus the MCP wrapper.
