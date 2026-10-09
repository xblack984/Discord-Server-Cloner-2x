"""HTTP client for the admin Dashboard (a FastAPI service). Contract documented in README."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..models import Command
from .protocols import DashboardError


class HttpDashboard:
    def __init__(self, base_url: str, token: str = "", retries: int = 3) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=45.0)
        self._retries = retries

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                resp = self._http.request(method, url, **kw)
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as e:
                last = e
                time.sleep(2**attempt)
        raise DashboardError(f"{method} {url} failed: {last}")

    def fetch_commands(self, wait_s: float) -> list[Command]:
        resp = self._request("GET", "/api/agent/commands", params={"wait": int(wait_s)})
        return [
            Command(id=str(c["id"]), type=str(c["type"]), payload=c.get("payload") or {})
            for c in resp.json()
        ]

    def ack(self, command: Command) -> None:
        self._request("POST", f"/api/agent/commands/{command.id}/ack")

    def post_event(self, kind: str, payload: dict[str, Any], image: bytes | None = None) -> None:
        meta = {"data": json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False)}
        files = {"image": ("screenshot.png", image, "image/png")} if image else None
        self._request("POST", "/api/agent/events", data=meta, files=files)
