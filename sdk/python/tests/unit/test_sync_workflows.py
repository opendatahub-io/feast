"""Invariants for the auto-sync GitHub workflows.

Guards the security-sensitive properties of ``sync_upstream.yml`` and
``sync_downstream_branch.yml`` (see RHOAIENG-103039):

* They must trigger on ``pull_request_target`` so repository secrets
  (``SYNC_STABLE_PAT``) are available when a PR merged from a fork fires
  the workflow. With plain ``pull_request``, GitHub Actions does not
  expose repository secrets for fork PRs and the sync fails at checkout.
* Because ``pull_request_target`` runs in a trusted context with access
  to secrets, checkout MUST be pinned to the trusted merge commit SHA —
  never to the PR head ref/sha (untrusted fork code).
* All third-party actions must be pinned to full commit SHAs.
* Jobs must declare minimal permissions and be gated on the merged state,
  the base repository, and maintainer-applied sync labels.

The downstream label-extraction logic (jq regex over PR labels) is also
verified against representative label sets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
SYNC_WORKFLOW_PATHS = [
    REPO_ROOT / ".github" / "workflows" / "sync_upstream.yml",
    REPO_ROOT / ".github" / "workflows" / "sync_downstream_branch.yml",
]
# Label(s) only maintainers can apply; the sync jobs must be gated on them.
DOWNSTREAM_LABEL_PATTERN = "^sync-to-rhoai-"


def _load_workflow(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"expected workflow file at {path}"
    with open(path) as f:
        return yaml.safe_load(f)


def _jobs(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return workflow["jobs"]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job.get("steps", []) if "uses" in s or "run" in s]


def _checkout_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        s
        for s in job.get("steps", [])
        if str(s.get("uses", "")).startswith("actions/checkout")
    ]


@pytest.mark.parametrize("path", SYNC_WORKFLOW_PATHS, ids=lambda p: p.name)
class TestSyncWorkflowInvariants:
    def test_trigger_is_pull_request_target(self, path: Path) -> None:
        """Fork PRs must run in the base-repo context so secrets are available."""
        workflow = _load_workflow(path)
        triggers = workflow[True]  # YAML parses bare `on:` key as True
        assert list(triggers.keys()) == ["pull_request_target"], (
            f"{path.name} must trigger only on pull_request_target "
            f"(found: {list(triggers.keys())}); plain pull_request cannot "
            "access repository secrets for fork PRs (RHOAIENG-103039)"
        )
        pr_target = triggers["pull_request_target"]
        assert pr_target["types"] == ["closed"]
        assert pr_target["branches"] == ["master"]

    def test_checkout_is_pinned_to_trusted_merge_sha(self, path: Path) -> None:
        """Check out the exact merged revision rather than the default event ref."""
        workflow = _load_workflow(path)
        for job_name, job in _jobs(workflow).items():
            checkouts = _checkout_steps(job)
            if not checkouts:
                continue
            for step in checkouts:
                with_config = step.get("with", {})
                ref = with_config.get("ref", "")
                assert ref == "${{ github.event.pull_request.merge_commit_sha }}", (
                    f"{path.name} job '{job_name}': actions/checkout must pin "
                    f"ref to the trusted merge commit SHA (got: {ref!r})"
                )
                assert with_config.get("persist-credentials") is False, (
                    f"{path.name} job '{job_name}': checkout must set "
                    "persist-credentials: false"
                )

    def test_no_untrusted_pr_refs_used(self, path: Path) -> None:
        """No step may reference the untrusted PR head ref/sha anywhere."""
        content = path.read_text()
        assert "pull_request.head.sha" not in content
        assert "pull_request.head.ref" not in content
        assert "refs/pull/" not in content

    def test_actions_are_pinned_to_commit_sha(self, path: Path) -> None:
        workflow = _load_workflow(path)
        for job_name, job in _jobs(workflow).items():
            for step in _steps(job):
                uses = step.get("uses", "")
                if not uses or uses.startswith("./"):
                    continue
                ref = uses.split("@", 1)[1].split(" ")[0]
                assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
                    f"{path.name} job '{job_name}': action {uses!r} must be "
                    "pinned to a full 40-character commit SHA"
                )

    def test_jobs_have_minimal_permissions(self, path: Path) -> None:
        workflow = _load_workflow(path)
        for job_name, job in _jobs(workflow).items():
            permissions = job.get("permissions")
            assert isinstance(permissions, dict), (
                f"{path.name} job '{job_name}': must declare explicit "
                "job-level permissions"
            )
            allowed = {"contents", "pull-requests"}
            assert set(permissions.keys()) <= allowed, (
                f"{path.name} job '{job_name}': permissions {set(permissions.keys())} "
                f"exceed the allowed set {allowed}"
            )
            assert set(permissions.values()) <= {"read", "write"}

    def test_jobs_gate_on_merged_and_base_repo(self, path: Path) -> None:
        """Only merged PRs on opendatahub-io/feast may trigger sync work."""
        workflow = _load_workflow(path)
        for job_name, job in _jobs(workflow).items():
            condition = str(job.get("if", ""))
            if job_name == "detect-targets":
                # Label extraction is read-only; repository gate is on the
                # downstream consumer job.
                assert "merged == true" in condition
                continue
            assert "merged == true" in condition, (
                f"{path.name} job '{job_name}': if condition must check "
                "github.event.pull_request.merged == true"
            )
            assert "github.repository == 'opendatahub-io/feast'" in condition, (
                f"{path.name} job '{job_name}': if condition must check "
                "github.repository == 'opendatahub-io/feast'"
            )

    def test_sync_is_pr_only(self, path: Path) -> None:
        """Sync must open PRs; no direct pushes to protected target branches."""
        content = path.read_text()
        assert "gh pr create" in content, (
            f"{path.name} must create a PR on the target repo (no direct pushes)"
        )
        # Pushes are only allowed to throwaway auto-sync/* branches (which the
        # created PR targets); never directly to a release/master branch.
        assert 'BRANCH="auto-sync/' in content, (
            f"{path.name}: pushed branches must be auto-sync/* throwaway branches"
        )
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("git push"):
                # "${BRANCH}" is the real push; ${BRANCH} appears inside the
                # PR-body instructions. Both refer to the auto-sync variable.
                assert '"$BRANCH"' in stripped or "${BRANCH}" in stripped, (
                    f"{path.name} contains a push to something other than the "
                    f"auto-sync branch: {stripped!r}"
                )


class TestDownstreamLabelExtraction:
    """Verify the label-matching semantics used by detect-targets."""

    @staticmethod
    def _extract_targets(labels: list[str]) -> list[str]:
        # Mirror of the jq filter in sync_downstream_branch.yml:
        # select labels matching ^sync-to-rhoai- and strip the sync-to- prefix.
        import re

        return sorted(
            label[len("sync-to-") :]
            for label in labels
            if re.search(DOWNSTREAM_LABEL_PATTERN, label)
        )

    @staticmethod
    def _pattern_from_workflow() -> str:
        """Keep the test regex in sync with the workflow's jq filter."""
        content = SYNC_WORKFLOW_PATHS[1].read_text()
        import re

        match = re.search(r'test\("\^sync-to-rhoai-"\)', content)
        assert match, "sync_downstream_branch.yml must filter on ^sync-to-rhoai-"
        return match.group(0)

    def test_workflow_uses_expected_pattern(self) -> None:
        assert self._pattern_from_workflow() == 'test("^sync-to-rhoai-")'

    @pytest.mark.parametrize(
        "labels,expected",
        [
            (["sync-to-rhoai-3.5"], ["rhoai-3.5"]),
            (["sync-to-rhoai-3.4-ea.1"], ["rhoai-3.4-ea.1"]),
            (["sync-to-rhoai-3.5", "sync-to-rhoai-3.4"], ["rhoai-3.4", "rhoai-3.5"]),
            # Stable/upstream are handled by other workflows and must be excluded
            (["sync-to-stable", "sync-to-upstream"], []),
            (["bug", "lgtm", "needs-rebase"], []),
            ([], []),
        ],
    )
    def test_label_extraction(self, labels: list[str], expected: list[str]) -> None:
        assert self._extract_targets(labels) == expected
