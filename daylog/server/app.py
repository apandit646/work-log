"""FastAPI app assembly: the JSON API under /api, plus the plain
HTML/CSS/JS web UI (../web/) served as static files at /.

This module only builds the app object; it never binds a socket. `daylog
ui` (cli.py) is what calls uvicorn.run(app, host=..., port=...) — and
host is always config.server.host, which config.py's validation
restricts to '127.0.0.1' or 'localhost'. There is no code path that can
bind this to 0.0.0.0.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import routes

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="daylog", description="Local-first daily activity tracker.")
    app.include_router(routes.router)
    # Mounted last / at "/" so it only catches what /api didn't already
    # handle; html=True serves index.html for "/" and unknown paths.
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
    return app


app = create_app()
