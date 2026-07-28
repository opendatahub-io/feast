# Cherry-Pick Sync Playbook

Label-driven GitHub Actions that cherry-pick merged PRs from `opendatahub-io/feast` master to upstream, stable, and downstream branches.

## Repository Map

```
feast-dev/feast              (upstream)
  ^
  | sync-to-upstream
  |
opendatahub-io/feast         (midstream — workflows live here)
  |
  |--- sync-to-stable -----> opendatahub-io/feast : stable
  |
  | sync-to-rhoai-*
  v
red-hat-data-services/feast  (downstream)
```

## Labels

| Label | Target Repo | Target Branch | Auto-merge | Workflow |
|-------|-------------|---------------|------------|----------|
| `sync-to-stable` | `opendatahub-io/feast` | `stable` | Yes | `sync_stable_branch.yml` |
| `sync-to-upstream` | `feast-dev/feast` | `master` | No | `sync_upstream.yml` |
| `sync-to-rhoai-<version>` | `red-hat-data-services/feast` | `rhoai-<version>` | Yes | `sync_downstream_branch.yml` |

Examples: `sync-to-rhoai-3.5` → `rhoai-3.5`, `sync-to-rhoai-3.4` → `rhoai-3.4`, `sync-to-rhoai-3.4-ea.1` → `rhoai-3.4-ea.1`

Multiple labels can be applied to a single PR. Each triggers independently.

## How to Use

1. Open a PR against `opendatahub-io/feast` master as usual.
2. Before or after approval, add the appropriate `sync-to-*` label(s).
3. Merge the PR.
4. The workflow automatically cherry-picks the merge commit and creates a PR in the target repo/branch.

If the cherry-pick is clean, the created PR auto-merges after CI passes (except upstream, which needs community review).

## When to Use Each Label

### `sync-to-stable`

Use when the change should be part of the current ODH release train. Typical cases:
- Bug fixes to the feast operator or server that affect shipped versions
- Configuration or manifest changes needed by ODH builds
- Security patches

Do **not** use for experimental or WIP changes — `stable` tracks what gets built and released.

### `sync-to-upstream`

Use when the change is a general improvement that the upstream feast community should have. Typical cases:
- Bug fixes not specific to ODH
- Feature enhancements applicable to all feast deployments
- Test improvements

The upstream PR will **not** auto-merge. Expect upstream maintainer review and possible revision requests.

### `sync-to-rhoai-*`

Use when the change needs to land in a specific RHOAI release branch. Typical cases:
- Backporting a fix to `rhoai-3.5` for the current GA release
- Backporting to `rhoai-3.4-ea.1` for an early-access build
- Applying a CVE fix across multiple release branches simultaneously

Apply multiple downstream labels if the fix needs to go to several branches (e.g., `sync-to-rhoai-3.5` + `sync-to-rhoai-3.4-ea.1`).

## Handling Conflicts

When the cherry-pick fails due to conflicts, the workflow still creates a PR but:
- The PR title includes `[CONFLICT]`
- A `needs-manual-resolution` label is added
- The PR body contains step-by-step instructions to resolve locally

To resolve:

```bash
# For stable conflicts
git fetch origin
git checkout -B auto-sync/stable/<PR_NUMBER> origin/stable
git cherry-pick -m 1 <MERGE_SHA>
# resolve conflicts
git add .
git cherry-pick --continue
git push origin auto-sync/stable/<PR_NUMBER> --force

# For downstream conflicts (e.g., rhoai-3.5)
git clone https://github.com/red-hat-data-services/feast.git && cd feast
git checkout auto-sync/rhoai-3.5/<PR_NUMBER>
git reset --hard origin/rhoai-3.5
git cherry-pick -m 1 <MERGE_SHA>
# resolve conflicts
git add .
git cherry-pick --continue
git push origin auto-sync/rhoai-3.5/<PR_NUMBER> --force

# For upstream conflicts
git clone https://github.com/feast-dev/feast.git && cd feast
git checkout auto-sync/upstream/<PR_NUMBER>
git reset --hard origin/master
git cherry-pick -m 1 <MERGE_SHA>
# resolve conflicts
git add .
git cherry-pick --continue
git push origin auto-sync/upstream/<PR_NUMBER> --force
```

## Prerequisites

The `SYNC_STABLE_PAT` repository secret needs push access to all three repos:
- `opendatahub-io/feast` (push branches, create PRs)
- `feast-dev/feast` (push branches, create PRs)
- `red-hat-data-services/feast` (push branches, create PRs)

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Workflow didn't trigger | PR wasn't merged, or label was added after merge | Re-open and re-merge, or manually cherry-pick |
| `target branch does not exist` error | Typo in label or branch hasn't been created yet | Verify branch name on the target repo |
| PR creation fails with 403 | `SYNC_STABLE_PAT` lacks permissions on the target repo | Update the PAT with appropriate repo access |
| Cherry-pick produces empty diff | The change already exists on the target branch | Close the auto-created PR |
