"""Trusted GitHub Actions entry point. Feature checkouts are data, never imported code."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

COMMANDS = {
    "spec-kit-atlassian": {"sync", "reconcile"},
    "agentstandards-atlassian": {"sync", "reconcile-decisions"},
}


def run(args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--control", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--owner", choices=COMMANDS, required=True)
    p.add_argument("--binding", default="")
    p.add_argument("--operation", default="sync")
    p.add_argument("--source-sha", default="")
    p.add_argument("--decision-key", default="")
    args = p.parse_args()
    if args.operation not in COMMANDS[args.owner] and args.operation != "auto":
        raise SystemExit("operation is not supported by this integration")
    config_path = Path(".specify/integrations/atlassian/config.yml")
    config = yaml.safe_load((args.control / config_path).read_text())
    if args.owner not in config["enabled_integrations"]:
        return
    bindings = [args.binding] if args.binding else config["binding_files"]
    if any(b not in config["binding_files"] for b in bindings):
        raise SystemExit("binding must be registered on the trusted default branch")
    if args.source_sha and not re.fullmatch(r"[a-f0-9]{40}", args.source_sha):
        raise SystemExit("source-sha must be a full commit SHA")
    env = dict(os.environ, ATLASSIAN_INTEGRATION_WORKER="1")
    env.pop("PYTHONPATH", None)
    for binding in bindings:
        ref = config["feature_refs"][binding]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", ref) or ".." in ref:
            raise SystemExit("invalid configured feature branch")
        run(["git", "fetch", "origin", f"refs/heads/{ref}"], args.data)
        tip = run(["git", "rev-parse", "FETCH_HEAD"], args.data)
        sha = args.source_sha or tip
        # Never publish an older feature commit over newer remote state.
        if sha != tip:
            raise SystemExit("dispatched revision is no longer the registered feature branch tip")
        run(["git", "checkout", "--detach", sha], args.data)
        data_config = yaml.safe_load((args.data / config_path).read_text())
        if data_config != config:
            raise SystemExit("feature integration configuration differs from trusted configuration")
        expected = json.loads((args.control / binding).read_text())
        actual = json.loads((args.data / binding).read_text())
        for field in ("repository_id", "feature_id", "feature_path"):
            if actual[field] != expected[field]:
                raise SystemExit("feature binding changed; update trusted registration first")
        operations = [args.operation]
        if args.operation == "auto":
            operations = [
                "sync",
                "reconcile" if args.owner == "spec-kit-atlassian" else "reconcile-decisions",
            ]
        for operation in operations:
            cmd = [
                args.owner,
                "--project",
                str(args.data.resolve()),
                operation,
                "--binding",
                binding,
                "--base",
                ref,
                "--apply",
            ]
            if operation == "reconcile-decisions" and args.decision_key:
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[0-9]+", args.decision_key):
                    raise SystemExit("invalid decision-key")
                cmd += ["--decision-key", args.decision_key]
            subprocess.run(cmd, cwd=args.control, env=env, check=True)


if __name__ == "__main__":
    main()
