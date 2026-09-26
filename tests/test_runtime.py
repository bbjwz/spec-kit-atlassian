import json
import sys
from pathlib import Path

import pytest
import yaml

from spec_kit_atlassian.common import runtime, worker
from spec_kit_atlassian.common.models import Conflict


def test_initialize_reuses_shared_identity(project, monkeypatch):
    def fake_run(args, root=None):
        return (
            json.dumps({"id": 123, "default_branch": "main"})
            if args[0] == "gh"
            else "feature/example"
        )

    monkeypatch.setattr(runtime, "run", fake_run)
    args = (
        project,
        "specs/001-feature",
        "example/project",
        "https://example.atlassian.net",
        "TEST",
        "10",
    )
    first = runtime.initialize(*args, "spec-kit-atlassian")
    second = runtime.initialize(*args, "agentstandards-atlassian")
    cfg, binding = runtime.load(project, first["binding"])
    assert first["binding"] == second["binding"]
    assert cfg.enabled_integrations == ["spec-kit-atlassian", "agentstandards-atlassian"]
    assert cfg.feature_refs[first["binding"]] == "feature/example"
    assert binding.repository_id == "123"


@pytest.mark.parametrize("tamper", ["sha", "config", "identity", "unregistered"])
def test_worker_rejects_untrusted_feature(tmp_path, cfg, binding, monkeypatch, tamper):
    control, data = tmp_path / "control", tmp_path / "data"
    relative = ".specify/integrations/atlassian/features/feature.json"
    cfg.enabled_integrations = ["spec-kit-atlassian"]
    cfg.binding_files = [relative]
    cfg.feature_refs = {relative: "feature/example"}
    for root in (control, data):
        (root / Path(relative).parent).mkdir(parents=True)
        (root / relative).write_text(binding.model_dump_json())
        (root / runtime.CONFIG).write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    source = "a" * 40
    if tamper == "sha":
        source = "b" * 40
    if tamper == "config":
        cfg.jira_project = "OTHER"
        (data / runtime.CONFIG).write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    if tamper == "identity":
        binding.repository_id = "456"
        (data / relative).write_text(binding.model_dump_json())
    if tamper == "unregistered":
        relative = "unknown.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "--control",
            str(control),
            "--data",
            str(data),
            "--owner",
            "spec-kit-atlassian",
            "--binding",
            relative,
            "--source-sha",
            source,
        ],
    )
    monkeypatch.setattr(worker, "run", lambda args, cwd=None: "a" * 40)
    monkeypatch.setattr(
        worker.subprocess, "run", lambda *a, **k: pytest.fail("untrusted code executed")
    )
    with pytest.raises(SystemExit):
        worker.main()


def test_doctor_does_not_require_run_or_feature_artifacts(project, cfg, binding, monkeypatch):
    from spec_kit_atlassian import cli

    monkeypatch.setattr(cli, "load", lambda *a: (cfg, binding))
    monkeypatch.setattr(cli, "restore_binding", lambda b, owner: b)
    monkeypatch.setattr(cli, "Cloud", lambda cfg: object())
    monkeypatch.setattr(cli, "doctor", lambda *a: {"ok": True})
    monkeypatch.setattr(cli, "revision", lambda *a, **k: pytest.fail("unnecessary Git access"))
    args = cli.parser().parse_args(
        ["--project", str(project), "doctor", "--binding", "feature.json"]
    )
    assert cli.execute(args) == {"ok": True}


def test_remote_writes_require_worker(monkeypatch):
    monkeypatch.delenv("ATLASSIAN_INTEGRATION_WORKER", raising=False)
    with pytest.raises(Conflict, match="serialized"):
        runtime.require_worker()
