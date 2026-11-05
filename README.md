# retrieval-harness

> Combine exact (ripgrep), semantic (ColGREP) and call-graph (codebase-memory-mcp) code search into one
> token-budgeted context for coding agents, with an optional local LLM judge and a per-run HTML trace.
> Works with Claude Code, VS Code/Copilot, any MCP client, or plain CLI. Research prototype.

**Status:** experimental. The pipeline, engines, adapters and evaluation scripts are implemented and
unit-tested; a full agent-level evaluation has not been run yet, so there are no claims here about
improved solve rates. Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and ripgrep. Everything
runs locally; the optional judge uses a local Ollama model and no API keys are needed.

Glue between existing retrieval tools and coding agents. It runs ripgrep (exact), ColGREP
(semantic) and codebase-memory-mcp (call graph) on a task, merges the results, and hands the
agent a token-budgeted Markdown context. No new retrieval engine. See [PLAN.md](PLAN.md).

## Setup
```bash
uv sync --extra graph --extra mcp      # codebase-memory-mcp + MCP server (both optional)
# ColGREP is not on PyPI: put the release binary on PATH or in .tools/bin/ (docs/tool-notes.md)
uv run rh init                         # build indexes, start the graph daemon
uv run rh doctor                       # missing | unindexed | stale | ok per engine
uv run rh retrieve --task "find all callers of parse_config and rename it"
```
ripgrep must be installed (`apt install ripgrep`, or it is provided by the dev group).
A missing or unindexed engine is skipped with a diagnostic; retrieval still returns.

## See what each stage did (trace)
Tracing is on by default. Every run writes `<repo>/.rh/trace/<timestamp>/trace.html` and `trace.json`
(CLI and hooks alike), refreshes `<repo>/.rh/trace/latest/`, and keeps the newest 20 runs. `.rh/` is git-ignored.
```bash
uv run rh retrieve --task "..."          # prints "trace: .../.rh/trace/<timestamp>/trace.html" on stderr
xdg-open .rh/trace/latest/trace.html
RH_TRACE=0                               # turn tracing off (or: rh retrieve --no-trace)
RH_TRACE_DIR=/some/dir                   # write somewhere else (or: rh retrieve --trace DIR)
```
`trace.html` is one self-contained page (no JavaScript, light and dark mode). A flow strip at the top
links to eight stages: task analysis, raw hits per engine, fusion, graph expansion (and why it ran),
excerpts (retrieved range → widened range), LLM judge (kept and dropped, with the prompt and raw
answer), token budget, and the exact text the agent receives. `trace.json` holds the same data.
Tracing never changes the result.

## LLM judge (local Ollama, on by default)
After excerpting, a local model (default `qwen3.5:4b`) drops excerpts it judges unnecessary. It only
removes excerpts, and only runs when there are more than `judge_min_items` (3).
- **Ollama is managed for you:** if nothing answers at `127.0.0.1:11434`, `rh` runs `ollama serve`
  and stops it again when the call ends. If Ollama was already running it is left running, but the
  model is unloaded right after answering (`keep_alive: 0`). Set `judge_autostart = false` to disable.
- **Pinned excerpts are never judged or dropped:** an excerpt that ripgrep matched and that contains a
  definition (`def`/`class`/`function`/`const x =` ...) whose name contains a prompt identifier (the code to
  change, or `test_<name>`) is kept without asking the model. Imports, fixtures and semantic-only
  neighbours are not pinned. Pinned excerpts are also left out of the prompt, so it is shorter.
- **Fails open:** if Ollama is missing, slow (`judge_timeout_s`, 60) or answers badly, everything is
  kept and a warning appears in `diagnostics`.
- **Not held to the 8 s deadline**, and a cold start (server + model load) adds seconds, so keep the
  hook `timeout` at 15 s or more.
- **Turn it off:** `RH_JUDGE=0`, `rh retrieve --no-judge`, or `judge = false` in `<repo>/.rh/rh.toml`.
  Other model: `judge_model = "..."` there, `RH_JUDGE_MODEL`, or `--judge-model`.
- The trace's "LLM judge" card shows the decision, prompt, raw answer and whether Ollama was started.

## Connect an agent
| Agent | How | Notes |
|---|---|---|
| Claude Code | `examples/claude-code/settings.json` (`UserPromptSubmit` hook) | context injected before the prompt; capped at 10,000 chars |
| VS Code / Copilot (+ Ollama) | `examples/vscode/` | hook writes `.rh/context.md`; instructions file tells the agent to read it |
| Any MCP client | `examples/mcp.json` | agent calls `retrieve_context` itself |
| Anything else | `examples/AGENTS.md.snippet` | agent runs `rh retrieve` |

## Evaluate
`eval/tasks/` is not committed (it is generated and holds local paths); create it with `make_tasks.py` first.
```bash
uv run python eval/make_tasks.py --repo /path/to/repo --n 20     # tasks from real commits
uv run python eval/run_retrieval.py --tasks eval/tasks           # recall@k, tokens, latency per engine combo
uv run python eval/run_agent.py --tasks eval/tasks --test-cmd "pytest -q"          # dry run: prints the cost bound
uv run python eval/run_agent.py --tasks eval/tasks --test-cmd "pytest -q" --execute # costs money
```

## Develop
`uv run pytest` (unit tests use recorded tool output in `tests/fixtures/`; `-m integration` calls the real tools).
