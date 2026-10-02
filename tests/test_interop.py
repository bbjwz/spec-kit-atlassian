"""Real companion publisher interoperability; CI supplies the immutable peer package."""

import pytest

peer = pytest.importorskip("agentstandards_atlassian.engine")
from agentstandards_atlassian.common.http import Cloud as CouncilCloud  # noqa: E402
from agentstandards_atlassian.council import CouncilSnapshot  # noqa: E402

from spec_kit_atlassian.engine import synchronize  # noqa: E402


@pytest.mark.parametrize("council_first", [True, False])
def test_independent_publishers_keep_one_feature_page(
    project, cfg, binding, cloud, tenant, monkeypatch, council_first
):
    # Isolate container interoperability; live constitution policy has its own fail-closed tests.
    monkeypatch.setattr("spec_kit_atlassian.governance.cli.feature_gate", lambda root: None)
    monkeypatch.setattr("spec_kit_atlassian.engine.revision", lambda *a, **k: "a" * 40)
    monkeypatch.setattr(peer, "revision", lambda *a, **k: "a" * 40)
    snap = CouncilSnapshot(
        run_id="test-run",
        feature_identity=binding.identity,
        source_revision="a" * 40,
        phase="created",
        outcome="CREATED",
        verified_ready=False,
        updated_at="2026-10-02T12:00:00Z",
        participants=[],
        reviewers=[],
        cells=[],
        manifest={"conflicts": []},
        decision_digest="b" * 64,
        usage={},
        warnings=[],
        errors=[],
        core_verification="Not ready",
        context_current=True,
    )
    monkeypatch.setattr(peer, "snapshot", lambda *a: snap)
    functions = [(peer.synchronize, CouncilCloud(cfg, cloud.client)), (synchronize, cloud)]
    if not council_first:
        functions.reverse()
    for function, adapter in functions:
        function(project, cfg, binding.model_copy(deep=True), adapter, lambda b: None)
    assert len(tenant.pages) == 1
    assert (
        sum(issue["fields"]["issuetype"]["name"] == "Epic" for issue in tenant.issues.values()) == 1
    )
    assert len(tenant.issues) == 4
    body = next(iter(tenant.pages.values()))["body"]["storage"]["value"]
    assert "Implementation plan" in body and "Architecture review" in body
    before = len(tenant.writes)
    for function, adapter in functions:
        function(project, cfg, binding.model_copy(deep=True), adapter, lambda b: None)
    assert len(tenant.writes) == before
