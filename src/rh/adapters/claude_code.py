"""Claude Code UserPromptSubmit hook: JSON on stdin, JSON with additionalContext on stdout.

Any failure prints nothing and exits 0 so the agent is never blocked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from rh.config import load_config
from rh.render import fit_chars, to_markdown
from rh.retrieve import retrieve
from rh.trace import env_trace, finish_env_trace


# Claude Code truncates additionalContext above 10,000 characters (keeping a 2,000-char preview),
# so stay under it. ~4 chars/token, with headroom for headings.
MAX_CHARS = 9_500
MAX_BUDGET_TOKENS = 2_200


def handle(payload: dict, profile: str | None = "claude-code") -> str:
    """Return the hook's stdout (empty string = inject nothing)."""
    prompt = payload.get("prompt") or ""
    repo = Path(payload.get("cwd") or ".")
    cfg = load_config(repo, profile)
    cfg.budget_tokens = min(cfg.budget_tokens, MAX_BUDGET_TOKENS)
    trace = env_trace()
    pkg = retrieve(repo, prompt, cfg, trace=trace)
    finish_env_trace(trace, pkg, cfg, repo)
    md = fit_chars(to_markdown(pkg, cfg.mermaid_min_edges), MAX_CHARS)
    if not md or not pkg.items:
        return ""
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": md,
    }})


def run(profile: str | None = "claude-code") -> int:
    try:
        out = handle(json.load(sys.stdin), profile)
        if out:
            sys.stdout.write(out)
    except Exception:
        pass
    return 0
