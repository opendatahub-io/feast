#!/usr/bin/env python3
"""Contract checks over rendered kustomize output.

Two historical failure modes are checked here:

1. A Deployment declares the same env var name twice. The API server keeps the
   last one, so the effective value silently differs from what the overlay
   appears to say (RHOAIENG-57579: duplicate RELATED_IMAGE_CRON_JOB).
2. The operator Deployment passes a flag the operator binary does not define.
   The container exits during flag parsing and never reconciles
   (RHOAIENG-100779: --metrics-cert-path missing from the 0.67 image).
"""

import argparse
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - handled by the caller
    sys.exit("PyYAML is required (pip install pyyaml)")


def load_documents(paths):
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for document in yaml.safe_load_all(handle):
                if isinstance(document, dict):
                    yield path, document


def _containers(document):
    """Yield (pod-spec-name, container) for every container in a workload."""
    kind = document.get("kind")
    if kind not in {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}:
        return
    spec = document.get("spec") or {}
    if kind == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec") or {})
    template = spec.get("template") or {}
    pod_spec = template.get("spec") or {}
    for container in pod_spec.get("containers") or []:
        yield document.get("metadata", {}).get("name", "?"), container


def check_duplicate_env(paths):
    failures = []
    for path, document in load_documents(paths):
        for name, container in _containers(document):
            seen = {}
            for entry in container.get("env") or []:
                key = entry.get("name")
                if key is None:
                    continue
                if key in seen:
                    failures.append(
                        f"{path}: {document.get('kind')}/{name} container "
                        f"{container.get('name')} declares env var {key} more than once"
                    )
                seen[key] = True
    return failures


def manager_flags(paths):
    """Flags passed to the operator container in the controller-manager Deployment."""
    flags = set()
    for _, document in load_documents(paths):
        if document.get("kind") != "Deployment":
            continue
        if "controller-manager" not in str(document.get("metadata", {}).get("name", "")):
            continue
        for _, container in _containers(document):
            argv = list(container.get("command") or []) + list(container.get("args") or [])
            for token in argv:
                if isinstance(token, str) and token.startswith("--"):
                    flags.add(token.split("=", 1)[0])
    return sorted(flags)


def help_flags(path):
    flags = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            token = stripped.split()[0].lstrip("-").split("=", 1)[0]
            if token:
                flags.add("--" + token)
    return flags


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    duplicate = sub.add_parser("duplicate-env")
    duplicate.add_argument("files", nargs="+")

    manager = sub.add_parser("manager-flags")
    manager.add_argument("files", nargs="+")

    help_parser = sub.add_parser("help-flags")
    help_parser.add_argument("file")

    args = parser.parse_args()

    if args.command == "duplicate-env":
        failures = check_duplicate_env(args.files)
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1 if failures else 0

    if args.command == "manager-flags":
        for flag in manager_flags(args.files):
            print(flag)
        return 0

    for flag in sorted(help_flags(args.file)):
        print(flag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
