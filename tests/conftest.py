from pathlib import Path

import httpx
import pytest

from spec_kit_atlassian.common.http import Cloud
from spec_kit_atlassian.common.models import Binding, Settings
from tests.fake_cloud import Tenant


@pytest.fixture
def cfg():
    return Settings(
        site="https://example.atlassian.net",
        github_repository="example/project",
        jira_project="TEST",
        confluence_space_id="10",
    )


@pytest.fixture
def binding():
    return Binding(
        repository_id="123",
        feature_id="11111111-1111-4111-8111-111111111111",
        feature_path="specs/001-feature",
    )


@pytest.fixture
def tenant():
    return Tenant()


@pytest.fixture
def cloud(cfg, tenant):
    return Cloud(cfg, httpx.Client(transport=httpx.MockTransport(tenant)))


@pytest.fixture
def project(tmp_path: Path, binding):
    feature = tmp_path / binding.feature_path
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Feature Specification: Sessions\n\nKeep user sessions.")
    (feature / "plan.md").write_text("# Plan\n\nUse a database.\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    (feature / "tasks.md").write_text(
        "## Phase 1\n\n- [ ] T001 [P] [US1] Build schema\n"
        "- [ ] T002 [US1] Add endpoint; depends on T001\n"
    )
    return tmp_path
