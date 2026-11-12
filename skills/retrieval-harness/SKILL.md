---
name: retrieval-harness
description: Gather relevant code context before editing. Runs ripgrep (exact), ColGREP (semantic) and a call graph together and returns token-budgeted excerpts, tests and callers as path:start-end. Use at the start of a coding task in an unfamiliar or large repo, e.g. "find all callers of X and rename it", "where is Y implemented", or before a multi-file change.
---

# Retrieval harness

`rh` merges exact search (ripgrep), semantic search (ColGREP) and call-graph expansion
(codebase-memory-mcp) into one token-budgeted Markdown context. It is a research prototype
that glues existing tools together; there is no hosted service and no API key.

## When to use
- Starting a task that touches code you have not read yet.
- You need callers, callees or tests of a symbol, not just text matches.
- Plain `grep` returned too much or too little.

Skip it for single-file edits where you already know the file.

## Setup (once per machine)
Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and ripgrep.

```bash
uv tool install "retrieval-harness[graph,mcp] @ git+https://github.com/Hemakumargokul/retrieval-harness"
```

ColGREP is not on PyPI. Put its release binary on `PATH`. See `docs/tool-notes.md` in the repo.
A missing engine is skipped with a diagnostic and retrieval still returns.

Once per target repo:

```bash
rh init      # builds the ColGREP and graph indexes
rh doctor    # per engine: missing | unindexed | stale | ok
```

## Use
From the target repo root, with a one-paragraph description of the task:

```bash
rh retrieve --task "find all callers of parse_config and rename it"
```

Useful flags: `--budget N` (token cap), `--no-judge` (skip the local Ollama filter),
`--format json`, `--repo PATH`.

## Reading the output
Excerpts are listed as `path:start-end`. Treat them as leads: open the files and verify
before relying on them. Each run also writes `.rh/trace/latest/trace.html`, which shows
what every stage kept or dropped. Open it if results look wrong.

## Notes
- The optional judge uses a local Ollama model and only removes excerpts. Disable with
  `RH_JUDGE=0` if Ollama is not installed.
- If `rh doctor` reports `stale`, rerun `rh init`.
- Other integrations (Claude Code hook, MCP server, VS Code): see `examples/` in the repo.
