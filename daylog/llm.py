"""Optional LLM polishing of the timesheet draft, via the Anthropic API.

Off by default (config.llm.enabled). The API key is read from the
ANTHROPIC_API_KEY environment variable — never from config.json, since
that file lives in plain text on disk and this tool otherwise makes no
network calls at all. The `anthropic` package itself is an optional
dependency (the `llm` extra: `pip install daylog[llm]`), imported only
when this module is actually used.

Falls back to the local rule-based draft (report.draft) on any failure —
no key, no package installed, no network, a rate limit, an API error —
so LLM polishing is always a pure enhancement on top of report
generation, never something that can make `daylog report` fail.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

_DEFAULT_MODEL = "claude-opus-5"
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You rewrite a software engineer's daily work log into a short list of plain, "
    "past-tense bullet points that a non-technical manager would understand, "
    "suitable for pasting directly into an office timesheet form.\n\n"
    "Rules:\n"
    "- One bullet per line, each starting with \"- \".\n"
    "- Plain business language: no jargon, no code identifiers, no commit hashes.\n"
    "- Preserve the same units of work as the input — do not invent new work, and "
    "do not drop or merge items beyond what the input already implies.\n"
    "- Output only the bullet list: no preamble, no headers, no closing remarks."
)


def polish_draft(lines: List[str], model: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Returns (polished_text, error). polished_text is None whenever
    polishing wasn't possible or failed for any reason — the caller
    should fall back to the plain rule-based draft in that case. Never
    raises."""
    if not lines:
        return None, "nothing to polish"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "ANTHROPIC_API_KEY is not set"

    try:
        import anthropic
    except ImportError:
        return None, "the 'anthropic' package is not installed (pip install daylog[llm])"

    raw_draft = "\n".join(f"- {line}" for line in lines)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model or _DEFAULT_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": raw_draft}],
        )
    except anthropic.AuthenticationError as exc:
        return None, f"Anthropic API authentication failed: {exc}"
    except anthropic.RateLimitError as exc:
        return None, f"Anthropic API rate limit hit: {exc}"
    except anthropic.APIConnectionError as exc:
        return None, f"could not reach the Anthropic API: {exc}"
    except anthropic.APIStatusError as exc:
        return None, f"Anthropic API error: {exc}"
    except Exception as exc:  # last-resort guard — must never break report generation
        return None, f"unexpected error calling the Anthropic API: {exc}"

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return None, "Anthropic API returned an empty response"
    return text, None
