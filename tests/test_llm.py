"""llm.py tests. The real `anthropic` package is used (installed as a dev
dependency) but every network call is mocked — client.messages.create is
monkeypatched directly, so these tests never make a real API call and
never need a real ANTHROPIC_API_KEY."""
import anthropic
import pytest

from daylog import llm


def _text_response(text):
    class _Block:
        type = "text"

    block = _Block()
    block.text = text

    class _Response:
        content = [block]

    return _Response()


def test_polish_draft_with_no_lines_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    text, error = llm.polish_draft([])
    assert text is None
    assert error is not None


def test_polish_draft_without_api_key_falls_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    text, error = llm.polish_draft(["Fixed a crash in the parser."])
    assert text is None
    assert "ANTHROPIC_API_KEY" in error


def test_polish_draft_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    captured = {}

    def fake_create(self, **kwargs):
        captured.update(kwargs)
        return _text_response("- Fixed a crash in the invoice parser.")

    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", fake_create)

    text, error = llm.polish_draft(["fix null ptr in parser"], model="claude-opus-5")

    assert error is None
    assert text == "- Fixed a crash in the invoice parser."
    assert captured["model"] == "claude-opus-5"
    assert "fix null ptr in parser" in captured["messages"][0]["content"]
    assert captured["output_config"] == {"effort": "low"}


def test_polish_draft_uses_default_model_when_none_given(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_create(self, **kwargs):
        captured.update(kwargs)
        return _text_response("- Did some work.")

    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", fake_create)
    llm.polish_draft(["did some work"])
    assert captured["model"] == "claude-opus-5"


def test_polish_draft_falls_back_on_authentication_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bad")

    def fake_create(self, **kwargs):
        request = __import__("httpx2").Request("POST", "https://api.anthropic.com/v1/messages")
        response = __import__("httpx2").Response(401, request=request, json={"error": {"message": "bad key"}})
        raise anthropic.AuthenticationError("bad key", response=response, body=None)

    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", fake_create)

    text, error = llm.polish_draft(["did some work"])
    assert text is None
    assert "authentication" in error.lower()


def test_polish_draft_falls_back_on_rate_limit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_create(self, **kwargs):
        request = __import__("httpx2").Request("POST", "https://api.anthropic.com/v1/messages")
        response = __import__("httpx2").Response(429, request=request, json={"error": {"message": "slow down"}})
        raise anthropic.RateLimitError("slow down", response=response, body=None)

    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", fake_create)

    text, error = llm.polish_draft(["did some work"])
    assert text is None
    assert "rate limit" in error.lower()


def test_polish_draft_falls_back_on_connection_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_create(self, **kwargs):
        request = __import__("httpx2").Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.APIConnectionError(request=request)

    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", fake_create)

    text, error = llm.polish_draft(["did some work"])
    assert text is None
    assert "reach the anthropic api" in error.lower()


def test_polish_draft_falls_back_on_empty_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_create(self, **kwargs):
        return _text_response("   ")

    monkeypatch.setattr(anthropic.resources.messages.Messages, "create", fake_create)

    text, error = llm.polish_draft(["did some work"])
    assert text is None
    assert "empty" in error.lower()


def test_polish_draft_falls_back_when_package_not_installed(monkeypatch):
    import builtins

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no module named anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    text, error = llm.polish_draft(["did some work"])
    assert text is None
    assert "not installed" in error
