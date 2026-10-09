"""FastAPI dashboard: the agent-facing contract + the admin web UI.

Agent side  (Authorization: Bearer <XBOX_AGENT_DASHBOARD_TOKEN>)
    GET  /api/agent/commands?wait=<s>      long-poll, each command is handed out once
    POST /api/agent/commands/{id}/ack
    POST /api/agent/events                 form field `data` = {"kind", "payload"} + optional file `image`
Admin side  (X-Admin-Token: <DASHBOARD_ADMIN_TOKEN>)
    GET  /api/admin/state | /api/admin/shots/{id}
    POST /api/admin/session | /command | /delete/confirm | /session/reset
The UI itself (/) is static and holds no secrets; it asks for the admin token.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .state import DashboardState, Refused

STATIC = Path(__file__).parent / "static"
MAX_IMAGE = 8 * 1024 * 1024
MAX_DATA = 200_000
MAX_WAIT_S = 25
CSP = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"


class NewSession(BaseModel):
    codes: str = ""
    stop_after: str | None = None


class AdminCommand(BaseModel):
    type: str


class DeleteConfirm(BaseModel):
    gamertag: str


class Answer(BaseModel):
    choice: str


class Pad(BaseModel):
    action: str
    value: str | None = None


def create_app(agent_token: str, admin_token: str, shots_dir: str | Path = "dashboard_shots") -> FastAPI:
    if not agent_token or not admin_token:
        raise ValueError("both the agent token and the admin token are required")
    state = DashboardState(shots_dir)
    app = FastAPI(title="XboxAgent dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.dash = state

    def agent_auth(authorization: str = Header(default="")) -> None:
        if not secrets.compare_digest(authorization.encode(), f"Bearer {agent_token}".encode()):
            raise HTTPException(401, "bad agent token")

    def admin_auth(x_admin_token: str = Header(default="")) -> None:
        if not secrets.compare_digest(x_admin_token.encode(), admin_token.encode()):
            raise HTTPException(401, "bad admin token")

    @app.exception_handler(Refused)
    async def refused(_, exc: Refused) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.middleware("http")
    async def headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        if not request.url.path.startswith("/api/"):
            resp.headers["Content-Security-Policy"] = CSP
        return resp

    # -------------------------------------------------------------- agent API

    @app.get("/api/agent/commands", dependencies=[Depends(agent_auth)])
    async def agent_commands(wait: int = 0) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(max(wait, 0), MAX_WAIT_S)
        while True:
            cmds = state.take_queued()
            if cmds or loop.time() >= deadline:
                return cmds
            await asyncio.sleep(0.25)

    @app.post("/api/agent/commands/{command_id}/ack", dependencies=[Depends(agent_auth)])
    async def agent_ack(command_id: str) -> dict[str, bool]:
        state.ack(command_id)
        return {"ok": True}

    @app.post("/api/agent/events", dependencies=[Depends(agent_auth)])
    async def agent_event(data: str = Form(...), image: UploadFile | None = File(default=None)) -> dict[str, bool]:
        if len(data) > MAX_DATA:
            raise HTTPException(413, "event too large")
        try:
            body = json.loads(data)
            kind, payload = body["kind"], body.get("payload") or {}
            if not isinstance(kind, str) or not isinstance(payload, dict):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "`data` must be JSON like {\"kind\": str, \"payload\": object}")
        png = None
        if image is not None:
            png = await image.read(MAX_IMAGE + 1)
            if len(png) > MAX_IMAGE:
                raise HTTPException(413, "image too large")
        state.ingest(kind, payload, png or None)
        return {"ok": True}

    # -------------------------------------------------------------- admin API

    @app.get("/api/admin/state", dependencies=[Depends(admin_auth)])
    async def admin_state() -> dict[str, Any]:
        return state.snapshot()

    @app.post("/api/admin/session", dependencies=[Depends(admin_auth)])
    async def admin_start(body: NewSession) -> dict[str, str]:
        return {"session_id": state.start_session(body.codes, body.stop_after or None).id}

    @app.post("/api/admin/command", dependencies=[Depends(admin_auth)])
    async def admin_command(body: AdminCommand) -> dict[str, str]:
        return {"command_id": state.command(body.type)["id"]}

    @app.post("/api/admin/delete/confirm", dependencies=[Depends(admin_auth)])
    async def admin_delete_confirm(body: DeleteConfirm) -> dict[str, str]:
        return {"command_id": state.confirm_delete(body.gamertag)["id"]}

    @app.post("/api/admin/answer", dependencies=[Depends(admin_auth)])
    async def admin_answer(body: Answer) -> dict[str, str]:
        return {"command_id": state.answer(body.choice)["id"]}

    @app.post("/api/admin/pad", dependencies=[Depends(admin_auth)])
    async def admin_pad(body: Pad) -> dict[str, str]:
        return {"command_id": state.pad(body.action, body.value)["id"]}

    @app.get("/api/admin/live", dependencies=[Depends(admin_auth)])
    async def admin_live() -> Response:
        if state.live is None:
            raise HTTPException(404, "no frame yet")
        _, frame = state.live
        return Response(frame, media_type="image/jpeg" if frame[:2] == b"\xff\xd8" else "image/png")

    @app.post("/api/admin/session/reset", dependencies=[Depends(admin_auth)])
    async def admin_reset() -> dict[str, bool]:
        state.reset()
        return {"ok": True}

    @app.get("/api/admin/shots/{shot_id}", dependencies=[Depends(admin_auth)])
    async def admin_shot(shot_id: str) -> Response:
        path = state.shot_path(shot_id)
        if path is None:
            raise HTTPException(404, "no such screenshot")
        return Response(path.read_bytes(), media_type="image/png")

    # -------------------------------------------------------------- UI

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
