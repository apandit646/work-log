"""The local JSON API. See routes.py for the endpoint list and app.py for
how the FastAPI app is assembled. Binds to 127.0.0.1 only — see `daylog
ui` (Phase 7) for how it's actually started; nothing here opens a socket
by itself, which is what makes it safe to import and test with FastAPI's
TestClient.
"""
