import httpx
import pytest

from spec_kit_atlassian.common.http import AmbiguousWrite, APIError, Cloud


def test_rate_limit_retry(cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    responses = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"ok": True}),
    ]
    cloud = Cloud(
        cfg, httpx.Client(transport=httpx.MockTransport(lambda request: responses.pop(0)))
    )
    assert cloud.request("jira", "GET", "/rest/api/3/myself") == {"ok": True}


def test_post_timeout_never_blind_retried(cfg):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private response details")

    cloud = Cloud(cfg, httpx.Client(transport=httpx.MockTransport(timeout)))
    with pytest.raises(AmbiguousWrite) as error:
        cloud.request("jira", "POST", "/rest/api/3/issue", json={})
    assert len(calls) == 1 and "private" not in str(error.value)


def test_redirect_not_followed(cfg):
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.invalid"})

    cloud = Cloud(cfg, httpx.Client(transport=httpx.MockTransport(redirect)))
    with pytest.raises(APIError):
        cloud.request("jira", "GET", "/rest/api/3/myself")
    assert len(calls) == 1
