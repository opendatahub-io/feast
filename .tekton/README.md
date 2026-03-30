# Tekton Pipelines (Pipelines-as-Code)

This directory contains Tekton `PipelineRun` definitions used for building and
testing the Open Data Hub (ODH) Feast components on the Konflux ITS OpenShift
environment. Pipelines are triggered by
[Pipelines-as-Code](https://pipelinesascode.com/) based on repository events
and comments.

For Tekton concepts (Tasks, Pipelines, PipelineRuns), see the
[Tekton Getting Started](http://tekton.dev/docs/getting-started/) documentation.

---

## Overview

```
PR opened / updated
  ├─ odh-feast-operator-pull-request    → quay.io/opendatahub/feast-operator:odh-pr-<sha>
  └─ odh-feature-server-pull-request   → quay.io/opendatahub/feature-server:odh-pr-<sha>
        │
        ├─ /group-test or group-test event  → feast-group-test   (tests PR images)
        └─ /pr-e2etest comment              → feast-pr-test      (tests PR images)

Merge to master
  ├─ odh-feast-operator-push            → quay.io/opendatahub/feast-operator:odh-master
  └─ odh-feature-server-push           → quay.io/opendatahub/feature-server:odh-master
        │
        └─ Daily 8 AM UTC cron /  → feast-nightly-test  (tests odh-master images)
```

All integration test pipelines provision an ephemeral HyperShift cluster
(m5.2xlarge, latest OCP 4.x via EaaS), deploy the Feast operator and feature
server, run the full test suite, collect must-gather artifacts, and post
results back to GitHub.

---

## Build pipelines

### `odh-feast-operator-pull-request.yaml` — Operator image build (PR)

Builds the Feast operator image from a pull request and pushes it to quay.io.
Required before running `/group-test` or `/pr-e2etest` on a PR.

| | |
|---|---|
| **Trigger** | Pull request opened or updated targeting `master` |
| **Source** | PR head branch at `{{revision}}` |
| **Build context** | `infra/feast-operator` |
| **Dockerfile** | `infra/feast-operator/Dockerfile` |
| **Output image** | `quay.io/opendatahub/feast-operator:odh-pr-<sha>` and `odh-pr-<pr-number>` |
| **Service account** | `build-pipeline-odh-feast-operator` |

---

### `odh-feature-server-pull-request.yaml` — Feature server image build (PR)

Builds the feature server image from a pull request and pushes it to quay.io.
Required before running `/group-test` or `/pr-e2etest` on a PR.

| | |
|---|---|
| **Trigger** | Pull request opened or updated targeting `master` |
| **Source** | PR head branch at `{{revision}}` |
| **Build context** | `sdk/python/feast/infra/feature_servers/multicloud` |
| **Dockerfile** | `sdk/python/feast/infra/feature_servers/multicloud/Dockerfile` |
| **Output image** | `quay.io/opendatahub/feature-server:odh-pr-<sha>` and `odh-pr-<pr-number>` |
| **Service account** | `build-pipeline-odh-feature-server` |

---

### `odh-feast-operator-push.yaml` — Operator image build (merge to master)

Builds and pushes the Feast operator image on every merge to `master`. This is
the image the nightly test pipeline resolves via the `odh-master` tag.

| | |
|---|---|
| **Trigger** | Push to `master` |
| **Source** | `master` branch |
| **Build context** | `infra/feast-operator` |
| **Dockerfile** | `infra/feast-operator/Dockerfile` |
| **Output image** | `quay.io/opendatahub/feast-operator:odh-master` |
| **Service account** | `build-pipeline-odh-feast-operator` |
| **Pipeline** | [`multi-arch-container-build.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/pipeline/multi-arch-container-build.yaml) |

---

### `odh-feature-server-push.yaml` — Feature server image build (merge to master)

Builds and pushes the feature server image on every merge to `master`. This is
the image the nightly test pipeline resolves via the `odh-master` tag.

| | |
|---|---|
| **Trigger** | Push to `master` |
| **Source** | `master` branch |
| **Build context** | `sdk/python/feast/infra/feature_servers/multicloud` |
| **Dockerfile** | `sdk/python/feast/infra/feature_servers/multicloud/Dockerfile` |
| **Output image** | `quay.io/opendatahub/feature-server:odh-master` |
| **Service account** | `build-pipeline-odh-feature-server` |
| **Pipeline** | [`multi-arch-container-build.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/pipeline/multi-arch-container-build.yaml) |

---

## Integration test pipelines

All three test pipelines run the same test suite on an ephemeral HyperShift
cluster. They differ only in what images they test and how they are triggered.

**Tests that run:**

| Test | Command | Description |
|------|---------|-------------|
| Feature Store Operator E2E | `make install && make deploy && make test-e2e` | Full operator + feature server e2e after deployment |
| Registry REST API | `uv run pytest ...test_registry_rest_api.py --integration` | Integration tests for the registry REST API |
| Previous-version compatibility | `make test-previous-version` | Validates compatibility with the previous operator version |
| Upgrade test | `make test-upgrade` | Validates upgrading the operator to the tested version |

After the test step, the pipeline runs must-gather (Feast + OpenShift),
pushes artifacts to the CI artifacts repo and OCI storage, and posts
results back to GitHub.

The test runner image is defined in
[`Dockerfile.go-its`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/integration-tests/feast/Dockerfile.go-its)
and includes Go, Python, `oc`/kubectl, `uv`, `skopeo`, and other tools.

---

### `feast-nightly-test.yaml` — Nightly integration test

Runs the full integration test suite against the latest `odh-master` images
built by the push pipelines. Tests master branch quality on a daily cadence.

| | |
|---|---|
| **Trigger** | Daily cron at **8 AM UTC** on `master` |
| **Images tested** | `quay.io/opendatahub/feast-operator:odh-master` and `quay.io/opendatahub/feature-server:odh-master` — resolved by digest at run time |
| **Source cloned** | `opendatahub-io/feast` at the commit embedded in the `odh-master` image label |
| **Pipeline** | [`nightly-testing-pipeline.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/integration-tests/feast/nightly-testing-pipeline.yaml) |
| **Result reporting** | Commit status `konflux/nightly-feast` posted to master HEAD; GitHub Actions monitors and opens issues on failure |

**How images are resolved**

The nightly pipeline does **not** build images inline. A `resolve-images` task
runs `skopeo inspect` on the `odh-master` tags and reads the
`io.openshift.build.commit.id` label to determine the exact source commit.
This ensures tests always run against properly Konflux-built images (feast
installed from PyPI, all assets present).

---

### `feast-group-test.yaml` — Group integration test

Runs integration tests using images built from the **current pull request**.
Use this when changes may affect both the Feast operator and the feature server
(e.g. API changes, shared library updates).

| | |
|---|---|
| **Trigger** | `group-test` event (from Konflux App Studio group-test workflow) |
| **Trigger (manual)** | Comment `/group-test` on a pull request |
| **Images tested** | PR-built images resolved by the `generate-snapshot` task (`odh-pr-<sha>`) |
| **Source cloned** | `opendatahub-io/feast` at the PR commit |
| **Pipeline** | [`pr-group-testing-pipeline.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/integration-tests/feast/pr-group-testing-pipeline.yaml) |
| **Result reporting** | GitHub check run `Red Hat Konflux / feast-group-test` posted to the PR by Pipelines-as-Code; PR comment with artifact browser link posted on completion |

**How images get into the group test**

1. When a PR is opened or updated, `odh-feast-operator-pull-request` and
   `odh-feature-server-pull-request` build and push images tagged
   `odh-pr-<sha>` and `odh-pr-<pr-number>`.
2. When `/group-test` is triggered, the `generate-snapshot` task finds those
   PR images by component name, assembles a snapshot JSON with their digests
   and git metadata, and passes it to `deploy-and-test`.

Both the images and the source checkout use the same PR commit, so the code
under test is always consistent.

---

### `feast-pr-test.yaml` — PR integration test (manual)

Identical test flow to `feast-group-test` but triggered manually by a PR
comment. Use this to run a full integration test on demand without waiting for
the group-test event.

| | |
|---|---|
| **Trigger (manual)** | Comment `/pr-e2etest` on a pull request |
| **Images tested** | PR-built images resolved by the `generate-snapshot` task (`odh-pr-<sha>`) |
| **Source cloned** | `opendatahub-io/feast` at the PR commit |
| **Pipeline** | [`pr-group-testing-pipeline.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/integration-tests/feast/pr-group-testing-pipeline.yaml) |
| **Max keep runs** | 5 (higher than group-test to retain more history for manual runs) |

---

## Trigger reference

| Comment / event | Pipeline | Images tested | When to use |
|-----------------|----------|---------------|-------------|
| PR opened/updated | `odh-feast-operator-pull-request` + `odh-feature-server-pull-request` | — (build only) | Automatic on every PR |
| `group-test` event or `/group-test` | `feast-group-test` | PR images | Multi-component changes |
| `/pr-e2etest` | `feast-pr-test` | PR images | On-demand full e2e on a PR |
| Daily 8 AM UTC `feast-nightly-test` | `odh-master` images | Master branch quality gate |
| Merge to master | `odh-feast-operator-push` + `odh-feature-server-push` | — (build only) | Automatic on every merge |

---

## Pipeline definitions

All integration test pipeline definitions live in
[`opendatahub-io/odh-konflux-central`](https://github.com/opendatahub-io/odh-konflux-central/tree/main/integration-tests/feast):

| File | Used by |
|------|---------|
| [`nightly-testing-pipeline.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/integration-tests/feast/nightly-testing-pipeline.yaml) | `feast-nightly-test` |
| [`pr-group-testing-pipeline.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/integration-tests/feast/pr-group-testing-pipeline.yaml) | `feast-group-test`, `feast-pr-test` |
| [`Dockerfile.go-its`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/integration-tests/feast/Dockerfile.go-its) | Test runner image for all test pipelines |
| [`multi-arch-container-build.yaml`](https://github.com/opendatahub-io/odh-konflux-central/blob/main/pipeline/multi-arch-container-build.yaml) | All build pipelines |
