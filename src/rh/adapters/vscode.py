"""VS Code (Local agent) / Copilot adapter.

VS Code's UserPromptSubmit hook cannot inject context (only `continue`, `stopReason` and
`systemMessage` are documented as output), and Copilot config-file hooks have their output
dropped. So instead of injecting, the hook *writes* the retrieved context to `.rh/context.md`,
and an instructions file (examples/vscode/.github/copilot-instructions.md) tells the agent to
read it. Whether the model follows that instruction is measured in the M4 evaluation.

The hook never blocks: it always prints `{"continue": true}`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from rh.config import load_config
from rh.render import to_json, to_markdown
from rh.retrieve import retrieve
from rh.trace import env_trace, finish_env_trace

CONTEXT_FILE = "context.md"
EMPTY_NOTE = "<!-- rh: no relevant context was found for the latest prompt -->\n"


def handle(payload: dict, profile: str | None = "local-small") -> dict:
    prompt = payload.get("prompt") or ""
    repo = Path(payload.get("cwd") or ".")
    cfg = load_config(repo, profile)
    trace = env_trace()
    pkg = retrieve(repo, prompt, cfg, trace=trace)
    finish_env_trace(trace, pkg, cfg, repo)
    out_dir = repo / ".rh"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = to_markdown(pkg, cfg.mermaid_min_edges) if pkg.items else ""
    (out_dir / CONTEXT_FILE).write_text(md or EMPTY_NOTE)
    (out_dir / "context.json").write_text(to_json(pkg))
    if not pkg.items:
        return {"continue": True}
    return {"continue": True,
            "systemMessage": f"rh: wrote {len(pkg.items)} excerpts (~{pkg.token_estimate} tokens) to .rh/{CONTEXT_FILE}"}


def run(profile: str | None = "local-small") -> int:
    result: dict = {"continue": True}
    try:
        result = handle(json.load(sys.stdin), profile)
    except Exception:
        pass
    sys.stdout.write(json.dumps(result))
    return 0
