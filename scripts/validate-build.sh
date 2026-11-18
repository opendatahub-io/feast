#!/usr/bin/env bash
set -euo pipefail

# Konflux PR build simulator for opendatahub-io/feast.
#
# Feast ships three images from this repository, all built by Konflux from
# .tekton/*pull-request.yaml:
#
#   odh-feature-server   sdk/python/feast/infra/feature_servers/multicloud/Dockerfile
#   odh-feast-operator   infra/feast-operator/Dockerfile
#   odh-feast-mcp        sdk/python/feast/mcp/docker/Dockerfile
#
# Most modes validate the inputs Konflux consumes. The `operator-e2e` mode goes
# further and deploys the images built here into a Kind cluster, because
# operator-e2e-integration-tests.yml builds the operator from source and never
# exercises the image this PipelineRun produces.
#
# Usage:
#   scripts/validate-build.sh <static|hermetic|manifests|operator|image|operator-e2e|all>
#
# Environment:
#   FEATURE_SERVER_IMAGE  Local tag for the feature-server image
#   MCP_IMAGE             Local tag for the MCP image
#   PLATFORM              Docker platform to build (default linux/amd64)
#   FEATURE_SERVER_PORT   Host port for the runtime check (default 16566)
#   KUSTOMIZE_BIN         kustomize binary (default: kustomize, else kubectl kustomize)
#   KUBECONFORM_BIN       kubeconform binary (default kubeconform)
#   KUBERNETES_VERSION    Kubernetes schema version for kubeconform
#   SKIP_MCP=1            Skip the MCP wrapper build
#   SKIP_OPERATOR=1       Skip the operator flag-contract check

MODE="${1:-all}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FEATURE_SERVER_DOCKERFILE="${FEATURE_SERVER_DOCKERFILE:-sdk/python/feast/infra/feature_servers/multicloud/Dockerfile}"
FEATURE_SERVER_REQUIREMENTS="${FEATURE_SERVER_REQUIREMENTS:-sdk/python/feast/infra/feature_servers/multicloud/requirements.txt}"
OPERATOR_DIR="${OPERATOR_DIR:-infra/feast-operator}"
MCP_DOCKERFILE="${MCP_DOCKERFILE:-sdk/python/feast/mcp/docker/Dockerfile}"
FEATURE_SERVER_IMAGE="${FEATURE_SERVER_IMAGE:-quay.io/opendatahub/feature-server:pr-validation}"
MCP_IMAGE="${MCP_IMAGE:-quay.io/opendatahub/odh-feast-mcp:pr-validation}"
OPERATOR_IMAGE="${OPERATOR_IMAGE:-quay.io/opendatahub/feast-operator:pr-validation}"
PLATFORM="${PLATFORM:-linux/amd64}"
FEATURE_SERVER_PORT="${FEATURE_SERVER_PORT:-16566}"
KUBECONFORM_BIN="${KUBECONFORM_BIN:-kubeconform}"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.31.0}"
MANIFEST_CONTRACT="${MANIFEST_CONTRACT:-scripts/manifest_contract.py}"
OPERATOR_NAMESPACE="${OPERATOR_NAMESPACE:-feast-operator-system}"
OPERATOR_DEPLOYMENT="${OPERATOR_DEPLOYMENT:-feast-operator-controller-manager}"
OPERAND_NAMESPACE="${OPERAND_NAMESPACE:-feast-sim-operand}"

info() { printf 'ℹ️  %s\n' "$*"; }
pass() { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
err() { printf '❌ %s\n' "$*" >&2; }

STATIC_ISSUES=0

static_error() {
  err "$*"
  STATIC_ISSUES=$((STATIC_ISSUES + 1))
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "Required command not found: $cmd"
    return 1
  fi
}

# Value of a PipelineRun parameter, e.g. param_value <file> dockerfile
param_value() {
  awk -v key="$2" '
    $0 ~ "- name: " key "$" { found = 1; next }
    found && /value:/ { sub(/^[[:space:]]*value:[[:space:]]*/, ""); print; exit }
  ' "$1"
}

check_konflux_targets() {
  info "Checking Konflux PipelineRun build targets..."
  local found=0
  local pr base context dockerfile

  for pr in .tekton/*pull-request.yaml; do
    [ -e "$pr" ] || continue
    base="$(basename "$pr")"

    # Early-gate and group-test pipelines are CI harnesses, not image builds.
    case "$base" in
      early-gate*|*group-test*|*nightly*) continue ;;
    esac

    found=$((found + 1))
    context="$(param_value "$pr" path-context)"
    dockerfile="$(param_value "$pr" dockerfile)"

    if [ -z "$dockerfile" ]; then
      static_error "$pr: could not parse the dockerfile parameter"
      continue
    fi

    context="${context:-.}"

    if [ ! -f "${context%/}/${dockerfile}" ]; then
      static_error "$pr: Dockerfile ${context%/}/${dockerfile} does not exist"
      continue
    fi

    if ! grep -q 'build-platforms' "$pr"; then
      warn "$pr: no build-platforms parameter; Konflux falls back to linux/x86_64 only"
    fi

    pass "$pr -> ${context%/}/${dockerfile}"
  done

  if [ "$found" -eq 0 ]; then
    static_error "No Konflux pull-request PipelineRuns with an image build were found"
  fi
}

check_python_inputs() {
  info "Checking the feature-server Python inputs..."

  if [ ! -f "$FEATURE_SERVER_REQUIREMENTS" ]; then
    static_error "Missing $FEATURE_SERVER_REQUIREMENTS"
    return
  fi

  local line line_no=0
  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    line="${line%%#*}"
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [ -z "$line" ] && continue

    if [[ "$line" != *"=="* ]]; then
      static_error "$FEATURE_SERVER_REQUIREMENTS:${line_no}: '$line' is not pinned with ==, so a hermetic prefetch cannot reproduce the build"
    fi
  done < "$FEATURE_SERVER_REQUIREMENTS"

  # The release CI rewrites line 2 of this file; moving the pin breaks it.
  local second
  second="$(sed -n '2p' "$FEATURE_SERVER_REQUIREMENTS")"
  if [[ "$second" != feast* ]]; then
    static_error "$FEATURE_SERVER_REQUIREMENTS: line 2 must be the feast pin (release CI rewrites it), found: ${second:-<empty>}"
  else
    pass "requirements.txt pins $(printf '%s' "$second" | tr -d '[:space:]')"
  fi

  # The image overlays the checkout inside the installed package. A missing
  # entrypoint here turns into an ImportError only at container start.
  if [ ! -f sdk/python/feast/__init__.py ]; then
    static_error "sdk/python/feast/__init__.py is missing; the Dockerfile overlay would install an unimportable package"
  fi
}

check_go_inputs() {
  info "Checking Go module inputs..."
  local dir

  for dir in . "$OPERATOR_DIR"; do
    if [ ! -f "$dir/go.mod" ]; then
      static_error "Missing $dir/go.mod"
      continue
    fi
    if [ ! -f "$dir/go.sum" ]; then
      static_error "Missing $dir/go.sum (required for a hermetic Go build)"
      continue
    fi
    if [ ! -s "$dir/go.sum" ]; then
      static_error "$dir/go.sum is empty"
      continue
    fi
    pass "$dir has go.mod and go.sum"
  done

  if [ -d "$OPERATOR_DIR" ] && [ -f "$OPERATOR_DIR/go.mod" ]; then
    if grep -q '^replace ' "$OPERATOR_DIR/go.mod"; then
      warn "$OPERATOR_DIR/go.mod has replace directives; confirm the replaced module is vendored or prefetched"
    fi
  fi
}

check_dockerfile_hygiene() {
  info "Checking Dockerfile inputs..."

  local dockerfile="$FEATURE_SERVER_DOCKERFILE"
  if [ ! -f "$dockerfile" ]; then
    static_error "Missing $dockerfile"
    return
  fi

  # Every COPY source must exist in the build context (the repository root).
  local src
  while IFS= read -r src; do
    [ -z "$src" ] && continue
    case "$src" in
      --from=*|/*) continue ;;
    esac
    if [ ! -e "$src" ]; then
      static_error "$dockerfile: COPY source '$src' does not exist in the build context"
    fi
  done < <(awk '
    /^[[:space:]]*COPY[[:space:]]/ {
      n = split($0, parts, /[[:space:]]+/)
      if (parts[2] ~ /^--from=/) next
      print parts[2]
    }
  ' "$dockerfile")

  if grep -Eq '^[[:space:]]*RUN[[:space:]].*curl[^|]*\|[[:space:]]*(sh|bash)' "$dockerfile"; then
    warn "$dockerfile downloads and executes a script at build time; a hermetic Konflux build has no network for this step"
  fi

  if grep -q 'sdk/python/feast /tmp/feast-overlay' "$dockerfile"; then
    pass "feature-server image overlays sdk/python/feast from the checkout"
  else
    warn "$dockerfile no longer overlays sdk/python/feast; the image would ship the released wheel instead of this PR"
  fi

  if [ -f .dockerignore ] && grep -Eq '^(sdk|sdk/python|sdk/python/feast)' .dockerignore; then
    static_error ".dockerignore excludes sdk/python, so the overlay COPY would fail"
  fi

  local op_dockerfile="$OPERATOR_DIR/Dockerfile"
  if [ -f "$op_dockerfile" ]; then
    if grep -q 'go build' "$op_dockerfile" && ! grep -q 'strictfipsruntime' "$op_dockerfile"; then
      warn "$op_dockerfile builds the operator without -tags strictfipsruntime; RHOAI FIPS builds require it"
    fi
    if grep -q 'FROM .*as builder' "$op_dockerfile"; then
      pass "operator image is multi-stage"
    fi
  else
    static_error "Missing $op_dockerfile"
  fi

  if [ -f "$MCP_DOCKERFILE" ]; then
    # A default BASE_TAG makes the MCP image silently float to a different base.
    if grep -Eq '^ARG BASE_TAG=' "$MCP_DOCKERFILE"; then
      static_error "$MCP_DOCKERFILE gives BASE_TAG a default; the MCP base image would be mutable"
    else
      pass "MCP Dockerfile requires an explicit BASE_TAG"
    fi
  fi
}

check_makefile_env_shadowing() {
  info "Checking that workflow env vars cannot shadow a Makefile variable..."
  local workflow=".github/workflows/pr-build-validation.yml"

  if [ ! -f "$workflow" ]; then
    warn "$workflow not found; cannot check for env shadowing"
    return
  fi

  # Keys two spaces deep under a top-level `env:` block.
  local keys
  keys="$(awk '/^env:/{inside=1; next} /^[^ #]/{inside=0} inside && /^  [A-Z0-9_]+:/{sub(/:.*/, ""); sub(/^  /, ""); print}' "$workflow")"

  local key shadows=""
  for key in $keys; do
    if grep -rEq "^${key}[[:space:]]*[:?+]*=" --include=Makefile . 2>/dev/null; then
      shadows="$shadows $key"
    fi
  done

  if [ -n "$shadows" ]; then
    err "workflow-level env var(s) are also defined in a Makefile:$shadows"
    err "  An exported value beats 'VAR ?=', so make silently uses the workflow value"
    err "  instead of the Makefile default. Prefix simulator variables with SIM_."
    exit 1
  fi

  pass "no workflow env var shadows a Makefile variable"
}

check_workflow_trigger_coverage() {
  info "Checking whether existing workflows build the image for sdk/python/feast changes..."
  local smoke=".github/workflows/docker_smoke_tests.yml"

  if [ ! -f "$smoke" ]; then
    warn "$smoke not found; cannot compare trigger coverage"
    return
  fi

  if grep -q '"sdk/python/feast/\*\*"' "$smoke"; then
    pass "docker-smoke-tests covers sdk/python/feast/**"
  else
    warn "docker-smoke-tests only triggers on a narrow path set; changes elsewhere under sdk/python/feast/** are not built by it (this workflow covers them)"
  fi

  if grep -q "sdk/python/feast" .github/workflows/operator-e2e-integration-tests.yml 2>/dev/null; then
    pass "operator e2e triggers on sdk/python/feast changes"
  else
    warn "operator e2e only triggers on infra/**; feature-server changes are not exercised through the operator there"
  fi
}

validate_static() {
  require_cmd grep
  require_cmd awk

  check_konflux_targets
  check_makefile_env_shadowing
  check_python_inputs
  check_go_inputs
  check_dockerfile_hygiene
  check_workflow_trigger_coverage

  echo
  if [ "$STATIC_ISSUES" -gt 0 ]; then
    err "$STATIC_ISSUES static validation issue(s) found"
    exit 1
  fi
  pass "Static validation passed"
}

run_kustomize() {
  local dir="$1"
  if command -v "${KUSTOMIZE_BIN:-kustomize}" >/dev/null 2>&1; then
    "${KUSTOMIZE_BIN:-kustomize}" build "$dir"
  else
    kubectl kustomize "$dir"
  fi
}

validate_manifests() {
  info "Validating operator kustomize overlays..."
  require_cmd kubectl

  local out_dir
  out_dir="$(mktemp -d)"
  local overlays="config/default config/overlays/odh config/overlays/rhoai config/overlays/openshift config/samples"
  local overlay name

  for overlay in $overlays; do
    name="$(printf '%s' "$overlay" | tr '/' '_')"
    if (cd "$OPERATOR_DIR" && run_kustomize "$overlay") > "${out_dir}/${name}.yaml"; then
      pass "kustomize build ${OPERATOR_DIR}/${overlay}"
    else
      err "kustomize build failed for ${OPERATOR_DIR}/${overlay}"
      exit 1
    fi
  done

  info "Checking rendered manifests for duplicate env var names..."
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    if ! python3 "$MANIFEST_CONTRACT" duplicate-env "${out_dir}"/*.yaml; then
      err "duplicate env var names in rendered manifests — the API server keeps only the last value"
      exit 1
    fi
    pass "no duplicate env var names in rendered manifests"
  else
    warn "PyYAML not installed; skipping duplicate env var check"
  fi

  if ! command -v "$KUBECONFORM_BIN" >/dev/null 2>&1; then
    warn "kubeconform not installed; skipping schema validation"
    return
  fi

  local file
  for file in "${out_dir}"/*.yaml; do
    local status=0
    # -verbose is what makes -output json emit statusSkipped records; without
    # it the built-in-kind guard below never sees a skipped resource.
    "$KUBECONFORM_BIN" -strict -ignore-missing-schemas -verbose \
      -kubernetes-version "$KUBERNETES_VERSION" -output json "$file" > "${file}.json" || status=$?

    if [ ! -s "${file}.json" ]; then
      err "kubeconform produced no report for $(basename "$file") (exit ${status}); refusing to pass without schema results"
      exit 1
    fi

    local violations
    violations="$(jq -r '
      .resources[] | select(.status == "statusInvalid" or .status == "statusError")
      | "  \(.kind) \(.name) (\(.version)): \(.msg)"' "${file}.json")" || {
      err "could not parse the kubeconform report for $(basename "$file")"
      exit 1
    }

    if [ -n "$violations" ] || [ "$status" -ne 0 ]; then
      err "kubeconform rejected $(basename "$file") (exit ${status}):"
      printf '%s\n' "${violations:-  (no per-resource detail was reported)}"
      exit 1
    fi

    local bad
    # kubeconform ships no schema for CustomResourceDefinition itself, so the
    # current API version is always reported as skipped. Everything else in a
    # built-in group that gets skipped is a typo or an API version that no
    # longer exists.
    bad="$(jq -r '
      .resources[] | select(.status == "statusSkipped")
      | . as $r
      | (if ($r.version | contains("/")) then ($r.version | split("/")[0]) else "" end) as $g
      | select(
          ($g == "" or ($g | endswith(".k8s.io")) or ($g | IN("apps", "batch", "policy", "autoscaling", "extensions")))
          and (($r.version != "apiextensions.k8s.io/v1") or ($r.kind != "CustomResourceDefinition"))
        )
      | "  \($r.kind) (\($r.version))"' "${file}.json")"

    if [ -n "$bad" ]; then
      err "built-in kind(s) skipped in $(basename "$file") — likely an apiVersion/kind typo:"
      printf '%s\n' "$bad"
      exit 1
    fi
  done

  pass "kubeconform schema validation passed"
}

validate_operator_contract() {
  info "Checking that the operator overlay only passes flags the binary defines..."
  require_cmd docker

  if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    warn "PyYAML not installed; skipping operator flag-contract check"
    return
  fi

  local out_dir
  out_dir="$(mktemp -d)"

  if (cd "$OPERATOR_DIR" && run_kustomize config/overlays/rhoai) > "${out_dir}/rhoai.yaml" &&
     (cd "$OPERATOR_DIR" && run_kustomize config/overlays/odh) > "${out_dir}/odh.yaml"; then
    pass "rendered operator overlays for flag extraction"
  else
    err "kustomize build failed while rendering the operator overlays"
    exit 1
  fi

  python3 "$MANIFEST_CONTRACT" manager-flags "${out_dir}/rhoai.yaml" "${out_dir}/odh.yaml"     | sort -u > "${out_dir}/used.txt"

  if [ ! -s "${out_dir}/used.txt" ]; then
    warn "no flags found on the controller-manager container; nothing to compare"
    return
  fi

  info "Building the operator image to read its flag set..."
  docker build --platform "$PLATFORM" \
    --file "$OPERATOR_DIR/Dockerfile" \
    --tag "$OPERATOR_IMAGE" \
    "$OPERATOR_DIR"

  docker run --rm --platform "$PLATFORM" "$OPERATOR_IMAGE" --help     > "${out_dir}/help.txt" 2>&1 || true

  if [ ! -s "${out_dir}/help.txt" ]; then
    err "operator binary produced no --help output; cannot verify the flag contract"
    exit 1
  fi

  python3 "$MANIFEST_CONTRACT" help-flags "${out_dir}/help.txt" | sort -u > "${out_dir}/known.txt"

  local missing
  missing="$(comm -23 "${out_dir}/used.txt" "${out_dir}/known.txt")"
  if [ -n "$missing" ]; then
    err "operator overlay passes flag(s) the binary does not define:"
    printf '%s\n' "$missing"
    err "the container would exit during flag parsing (see RHOAIENG-100779)"
    exit 1
  fi

  pass "every flag passed by the operator overlays is defined by the binary"
}

validate_hermetic_python() (
  info "Proving the feature-server Python inputs resolve and install without network..."

  require_cmd uv
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp:-}"' EXIT

  local pyver="${HERMETIC_PYTHON_VERSION:-3.12}"
  local platform="${HERMETIC_PYTHON_PLATFORM:-x86_64-manylinux_2_34}"
  local lock="${tmp}/lock.txt"

  # pip cannot resolve this graph ("resolution-too-deep"), which is why the
  # image installs with uv. Resolve with the same tool and pin the whole
  # closure for the Konflux target platform.
  info "Resolving requirements.txt for ${platform} (python ${pyver})..."
  uv pip compile "$FEATURE_SERVER_REQUIREMENTS" \
    --python-version "$pyver" \
    --python-platform "$platform" \
    --quiet \
    --output-file "$lock"
  pass "dependency closure resolves ($(grep -c '==' "$lock") pinned distributions)"

  if [ "$(uname -s)" != "Linux" ]; then
    warn "Skipping the offline install on $(uname -s); only the ${platform} resolution was checked"
    return
  fi

  # An offline install only proves the resolved closure when the interpreter
  # matches the platform the lock was resolved for. A native-only run is
  # reported as such instead of claiming target-platform coverage.
  local target_arch="${platform%%-*}"
  if [ "$(uname -m)" != "$target_arch" ]; then
    warn "Skipping the offline install on $(uname -m); the lock targets ${target_arch} (${platform})"
    return
  fi

  export UV_CACHE_DIR="${tmp}/uv-cache"

  # --python-platform keeps wheel selection at the image's glibc level
  # (UBI 9 is manylinux_2_34); without it a newer-glibc runner can prefer a
  # wheel the image cannot use and the cache would miss the one it needs.
  info "Populating the prefetch cache from the resolved ${platform} lock..."
  uv venv "${tmp}/v1" --python "$pyver" --quiet
  uv pip install --python "${tmp}/v1/bin/python" --python-platform "$platform" \
    --requirement "$lock" --quiet

  info "Installing requirements.txt with --offline from that cache only..."
  rm -rf "${tmp}/v1"
  uv venv "${tmp}/v2" --python "$pyver" --quiet
  uv pip install --offline --python "${tmp}/v2/bin/python" --python-platform "$platform" \
    --requirement "$FEATURE_SERVER_REQUIREMENTS" --quiet
  pass "requirements.txt installs with no network"

  # Mirror the Dockerfile overlay: the checkout must be importable on top of
  # the resolved environment, not just syntactically valid.
  local site
  site="$("${tmp}/v2/bin/python" -c 'import os, feast; print(os.path.dirname(feast.__file__))')"
  cp -R sdk/python/feast/. "$site/"
  "${tmp}/v2/bin/python" -c 'import feast.feature_server, feast.feature_store, feast.infra.registry, feast.repo_config; print("overlay imports OK")'
  pass "sdk/python/feast overlays cleanly onto the resolved environment"
)

validate_hermetic_go() {
  require_cmd go

  local goos goarch
  goos="${PLATFORM%%/*}"
  goarch="${PLATFORM#*/}"
  goarch="${goarch%%/*}"
  if [ "$goarch" = "$PLATFORM" ]; then
    err "PLATFORM must be os/arch (got ${PLATFORM})"
    exit 1
  fi
  case "$goarch" in
    x86_64) goarch=amd64 ;;
    aarch64) goarch=arm64 ;;
  esac
  info "Proving the operator builds with GOPROXY=off for ${goos}/${goarch}..."

  # GONOPROXY=none removes the direct-VCS fallback: with only GOPROXY=off a
  # module matching GONOPROXY/GOPRIVATE could still be fetched over the network
  # and the preflight would pass without the module being in the cache.
  ( cd "$OPERATOR_DIR" && go mod download )
  ( cd "$OPERATOR_DIR" && GOPROXY=off GONOPROXY=none GOFLAGS=-mod=readonly CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" go build ./... )
  ( cd "$OPERATOR_DIR" && GOPROXY=off GONOPROXY=none GOFLAGS=-mod=readonly CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" go vet ./... )
  ( cd "$OPERATOR_DIR" && go mod verify )
  pass "infra/feast-operator builds and vets with no module proxy for ${goos}/${goarch}"
}

validate_image() {
  info "Building the Konflux feature-server image..."
  require_cmd docker

  docker build --platform "$PLATFORM" \
    --file "$FEATURE_SERVER_DOCKERFILE" \
    --tag "$FEATURE_SERVER_IMAGE" \
    . 

  local container="feast-sim-$$"
  trap 'docker rm -f "${container:-}" >/dev/null 2>&1 || true' RETURN

  info "Starting the feature server..."
  docker run -d --rm --name "$container" \
    --platform "$PLATFORM" \
    -p "${FEATURE_SERVER_PORT}:6566" \
    -v "${REPO_ROOT}/infra/scripts/feature_server_docker_smoke.py:/smoke.py:ro" \
    "$FEATURE_SERVER_IMAGE" python /smoke.py >/dev/null

  local ready=false i
  for i in $(seq 1 60); do
    if curl -fsS "http://localhost:${FEATURE_SERVER_PORT}/health" >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 2
  done

  if [ "$ready" != "true" ]; then
    err "feature-server /health did not become ready"
    docker logs "$container" || true
    exit 1
  fi
  pass "feature server reached /health"

  docker exec "$container" python -c 'import feast, feast.feature_server; print("import OK")' >/dev/null
  pass "image imports the overlaid feast package"

  docker rm -f "$container" >/dev/null 2>&1 || true

  if [ "${SKIP_MCP:-0}" = "1" ]; then
    warn "SKIP_MCP=1; not building the MCP wrapper"
    return
  fi

  info "Building the MCP wrapper on top of the PR feature-server image..."
  docker build \
    --file "$MCP_DOCKERFILE" \
    --build-arg "BASE_IMAGE=${FEATURE_SERVER_IMAGE%:*}" \
    --build-arg "BASE_TAG=${FEATURE_SERVER_IMAGE##*:}" \
    --tag "$MCP_IMAGE" \
    .

  docker run --rm "$MCP_IMAGE" --help >/dev/null
  pass "MCP wrapper builds on the PR image and its entrypoint runs"
}

dump_operator_debug() {
  echo "--- operator pods ---" >&2
  kubectl -n "$OPERATOR_NAMESPACE" get pods -o wide >&2 || true
  echo "--- operator logs (tail) ---" >&2
  kubectl -n "$OPERATOR_NAMESPACE" logs -l control-plane=controller-manager --tail=80 >&2 || true
  echo "--- operand pods ---" >&2
  kubectl -n "$OPERAND_NAMESPACE" get pods -o wide >&2 || true
  echo "--- operand events ---" >&2
  kubectl -n "$OPERAND_NAMESPACE" get events --sort-by=.lastTimestamp >&2 | tail -30 || true
}

# validate_operator_e2e proves the Konflux-built operator image actually runs and
# reconciles, and that the operand it renders is the Konflux-built feature server.
#
# The repository e2e suite does deploy an operator, but it builds that operator
# from source (`DeployOperatorFromCode`), so a container-level break in the image
# this PipelineRun publishes is invisible to it.
#
# The cluster teardown is explicit: a RETURN trap here would also fire when the
# helper called by this function returns, and delete the cluster mid-run.
validate_operator_e2e() {
  local cluster="feast-sim-$$"
  local rc=0

  run_operator_e2e "$cluster" || rc=$?

  if [ "$rc" -ne 0 ]; then
    dump_operator_debug
  fi

  kind delete cluster --name "$cluster" >/dev/null 2>&1 || true

  return "$rc"
}

run_operator_e2e() {
  local cluster="$1"

  info "Deploying the Konflux operator image and reconciling a FeatureStore..."
  require_cmd docker
  require_cmd kind
  require_cmd kubectl

  info "Building the Konflux operator image from $OPERATOR_DIR/Dockerfile..."
  docker build --platform "$PLATFORM" \
    --file "$OPERATOR_DIR/Dockerfile" \
    --tag "$OPERATOR_IMAGE" \
    "$OPERATOR_DIR"
  pass "operator image built: $OPERATOR_IMAGE"

  info "Building the Konflux feature-server image..."
  docker build --platform "$PLATFORM" \
    --file "$FEATURE_SERVER_DOCKERFILE" \
    --tag "$FEATURE_SERVER_IMAGE" \
    .
  pass "feature-server image built: $FEATURE_SERVER_IMAGE"

  info "Creating a Kind cluster..."
  kind create cluster --name "$cluster" --wait 5m >/dev/null
  pass "kind cluster $cluster is ready"

  kind load docker-image "$OPERATOR_IMAGE" --name "$cluster" >/dev/null
  kind load docker-image "$FEATURE_SERVER_IMAGE" --name "$cluster" >/dev/null
  pass "both images are loaded into the cluster"

  info "Installing the CRDs and deploying the operator with the PR images..."
  ( cd "$OPERATOR_DIR" && make install >/dev/null )
  ( cd "$OPERATOR_DIR" && make deploy IMG="$OPERATOR_IMAGE" FS_IMG="$FEATURE_SERVER_IMAGE" >/dev/null )

  # A crash-looping container still satisfies "created", so wait on availability
  # rather than on the object existing.
  if ! kubectl -n "$OPERATOR_NAMESPACE" wait \
    --for=condition=Available "deployment/$OPERATOR_DEPLOYMENT" --timeout=6m; then
    err "the operator deployment did not become Available; the image does not run"
    return 1
  fi
  pass "operator deployment is Available"

  local deployed_operator_image
  deployed_operator_image="$(kubectl -n "$OPERATOR_NAMESPACE" get deployment "$OPERATOR_DEPLOYMENT" \
    -o jsonpath='{.spec.template.spec.containers[0].image}')"
  if [ "$deployed_operator_image" != "$OPERATOR_IMAGE" ]; then
    err "the running operator is '$deployed_operator_image', not '$OPERATOR_IMAGE'"
    return 1
  fi
  pass "the running operator is the image built here"

  info "Creating a FeatureStore so the operator has to reconcile..."
  kubectl create namespace "$OPERAND_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl apply -n "$OPERAND_NAMESPACE" -f - >/dev/null <<'MANIFEST'
apiVersion: feast.dev/v1
kind: FeatureStore
metadata:
  name: konflux-sim
spec:
  feastProject: konflux_sim
  services:
    offlineStore:
      server: {}
    registry:
      local:
        server: {}
MANIFEST
  pass "FeatureStore/konflux-sim created"

  # The operand deployments are what the operator renders from the CR. If the
  # operator cannot reconcile, or renders an image that is not the one built
  # here, this never becomes Available.
  if ! kubectl -n "$OPERAND_NAMESPACE" wait \
    --for=condition=Available deployment --all --timeout=10m; then
    err "the operand deployments did not become Available; the operator did not reconcile"
    return 1
  fi
  pass "every operand deployment is Available"

  local images
  images="$(kubectl -n "$OPERAND_NAMESPACE" get pods \
    -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}')"

  if ! printf '%s\n' "$images" | grep -qx "$FEATURE_SERVER_IMAGE"; then
    err "no operand pod runs the feature-server image built here ($FEATURE_SERVER_IMAGE):"
    printf '%s\n' "$images" | sed 's/^/   /'
    return 1
  fi
  pass "the operand runs the feature-server image built here"

  local restarts
  restarts="$(kubectl -n "$OPERAND_NAMESPACE" get pods \
    -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}' \
    | awk '{sum += $1} END {print sum + 0}')"
  if [ "$restarts" -gt 2 ]; then
    err "operand containers restarted $restarts times; the image is not stable"
    return 1
  fi
  pass "operand containers are stable ($restarts restarts)"

  pass "the Konflux operator image reconciles and ships the Konflux feature server"
  return 0
}

case "$MODE" in
  static)    validate_static ;;
  hermetic)  validate_hermetic_python; validate_hermetic_go ;;
  manifests) validate_manifests ;;
  operator)  validate_operator_contract ;;
  image)     validate_image ;;
  operator-e2e) validate_operator_e2e ;;
  all)       validate_static; validate_hermetic_python; validate_hermetic_go; validate_manifests; validate_operator_contract; validate_image ;;
  *)
    err "Unknown mode: $MODE"
    echo "Usage: $0 <static|hermetic|manifests|operator|image|all>" >&2
    exit 2
    ;;
esac
