"""FastAPI app assembly. JSON API only here — Phase 7 adds static file
serving for the web UI on top of this same app.

This module only builds the app object; it never binds a socket. `daylog
ui` (Phase 8's packaging work covers autostart, but the command itself
lands in Phase 7) is what calls uvicorn.run(app, host=..., port=...) —
and host is always config.server.host, which config.py's validation
restricts to '127.0.0.1' or 'localhost'. There is no code path that can
bind this to 0.0.0.0.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import routes


def create_app() -> FastAPI:
    app = FastAPI(title="daylog", description="Local-first daily activity tracker — JSON API.")
    app.include_router(routes.router)
    return app


app = create_app()
