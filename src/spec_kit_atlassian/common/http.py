from __future__ import annotations

import os
import time
from typing import Any

import httpx

from .models import Settings


class APIError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


class AmbiguousWrite(APIError):
    """A write might have succeeded. Reconcile identity before retrying."""


class Cloud:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        if client is None:
            email = os.environ.get(settings.email_env)
            token = os.environ.get(settings.token_env)
            if not email or not token:
                raise ValueError("Atlassian credential environment variables are not configured")
            client = httpx.Client(
                auth=httpx.BasicAuth(email, token),
                timeout=30,
                follow_redirects=False,
                trust_env=False,
            )
        self.client = client

    def request(self, service: str, method: str, path: str, **kwargs: Any) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("relative API path required")
        cfg = self.settings
        if cfg.auth_mode == "scoped-token":
            if cfg.cloud_id is None:
                raise ValueError("scoped-token authentication requires cloud_id")
            origin = f"https://api.atlassian.com/ex/{service}/{cfg.cloud_id}"
        else:
            origin = cfg.site
        url = origin + path
        for attempt in range(4):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                if method != "GET":
                    raise AmbiguousWrite(
                        0, "write outcome unknown; reconcile before retry"
                    ) from None
                if attempt == 3:
                    raise APIError(0, "transport failed") from None
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 or (method == "GET" and response.status_code >= 500):
                if attempt < 3:
                    try:
                        delay = float(response.headers.get("Retry-After", 2**attempt))
                    except ValueError:
                        delay = 2**attempt
                    time.sleep(min(max(delay, 0), 30))
                    continue
            if response.status_code >= 500 and method != "GET":
                raise AmbiguousWrite(response.status_code, "write outcome unknown")
            if response.status_code >= 300:
                # Response bodies and URLs can contain private data: do not echo them.
                raise APIError(response.status_code, "Atlassian request failed")
            return response.json() if response.content else None
        raise APIError(429, "retry budget exhausted")
