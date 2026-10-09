"""python -m xbox_agent.dashboard   (needs: pip install -r xbox_agent/requirements-dashboard.txt)"""
from __future__ import annotations

import os
import sys

from ..config import load_env_file

HINT = 'generate one with: python -c "import secrets; print(secrets.token_urlsafe(24))"'


def main() -> int:
    load_env_file()
    agent_token = os.environ.get("XBOX_AGENT_DASHBOARD_TOKEN", "")
    admin_token = os.environ.get("DASHBOARD_ADMIN_TOKEN", "")
    missing = [n for n, v in (("XBOX_AGENT_DASHBOARD_TOKEN", agent_token), ("DASHBOARD_ADMIN_TOKEN", admin_token)) if not v]
    if missing:
        print(f"Missing {', '.join(missing)} ({HINT}).", file=sys.stderr)
        return 2
    if agent_token == admin_token:
        print("XBOX_AGENT_DASHBOARD_TOKEN and DASHBOARD_ADMIN_TOKEN must be different.", file=sys.stderr)
        return 2

    import uvicorn

    from .app import create_app

    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run(create_app(agent_token, admin_token, os.environ.get("DASHBOARD_SHOTS_DIR", "dashboard_shots")),
                host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
