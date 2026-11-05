# retrieval-harness — Plan

**Goal:** a thin orchestration harness that pulls in context from existing retrieval tools and
adds it to any coding agent's prompt. We reuse existing search tools and write only the code
that connects them.

**Research question:** does combining exact search, semantic search and a code graph give
better coding results, at a lower total cost, than using the best single tool on its own?

> **Status (2026-10-19):** implemented and tested: the core pipeline, the ripgrep, ColGREP and graph
> engines, `rh init|doctor|retrieve|hook|mcp`, the Claude Code / VS Code / MCP adapters, and the
> evaluation scripts. Not yet done: a live run of any agent, and a real evaluation repo.
> ColGREP is used for semantic `--json` search only (agent mode is deliberately not used). See [docs/tool-notes.md](docs/tool-notes.md) for
> measured behaviour of the tools.

---

## 1. Architecture: one core command, thin per-agent adapters

```
                ┌──────────── adapters (one small file per agent) ───────────┐
 Claude Code    │ UserPromptSubmit hook → rh hook claude-code                 │
 VS Code/Copilot│ hook writes .rh/context.md + instructions file (see §4)      │
 Codex/others   │ hook if supported, else MCP tool or AGENTS.md fallback      │
 Any agent      │ MCP server exposing one tool: retrieve_context(task)        │
 Humans/CI/eval │ rh retrieve --repo . --task "..."                           │
                └──────────────────────────────┬──────────────────────────────┘
                                               ▼
                          core:  rh retrieve  →  ContextPackage (JSON)
                                               ▼
                              renderers: context.md / context.json / agent payload
```

- **Core** (`rh retrieve`): takes a repo and a task, returns a `ContextPackage`. It contains no code for any particular agent.
- **Adapters**: about 30 lines each. Each one reads that agent's hook payload, calls the core
  and prints output in the format that agent expects. Supporting a new agent means writing one
  new adapter.
- **Integration tiers**, from best to fallback:
  1. A hook adds the context before every prompt.
  2. An MCP tool the agent calls when it decides to.
  3. The CLI plus an instruction in AGENTS.md.
- MCP only exposes tools to an agent; it does not make retrieval run before every prompt.
  Running on every prompt requires a hook.

### Engines (none are built here)

| Engine | Role | How it is called |
|---|---|---|
| ripgrep | Exact search: identifiers, error strings, config keys, tests, docs | `rg --json` |
| ColGREP | Semantic code search | `colgrep --json -k N "<query>" .` (JSON mode exists; its `matches` list holds the real sub-units) |
| codebase-memory-mcp | Graph: callers, callees | `codebase-memory-mcp cli query_graph --format json` with a Cypher query (`trace_path` returns no file/line numbers). Needs the daemon running: ~3.6 s/call warm vs ~10 s cold. |
| Serena | *Deferred.* An alternative graph engine, added only if the M3 evaluation shows a gap | none |

We do not use codebase-memory-mcp's built-in semantic search, because ColGREP already covers it.

---

## 2. Prerequisites and index lifecycle

| Engine | One-time setup | Index build | Keeping it fresh |
|---|---|---|---|
| ripgrep | Already installed | None | None |
| ColGREP | `brew install lightonai/tap/colgrep` (or the shell installer). | `colgrep init <repo>`. The first build downloads the embedding model and embeds the whole repo. | Automatic: each search updates the index for changed files first. This cost lands inside the hook's time limit. |
| codebase-memory-mcp | PyPI: `uv sync --extra graph` (downloads and verifies the native binary on first run) | `rh init` starts the daemon, then runs `codebase-memory-mcp cli index_repository --repo-path <repo>`. | Not automatic: `rh init --if-stale --background` re-indexes when HEAD changed. |

### Harness commands

- **`rh init`** builds both indexes and writes `.rh/state.json`:
  - the graph project name (later graph queries need `--project`)
  - the ColGREP model
  - when each index was built
  - the git HEAD at build time
- **`rh doctor`** reports each engine as `missing | unindexed | stale | ok`.

### Rules

- The per-prompt hook **never** builds an index.
- If the HEAD recorded for the graph index differs from the current HEAD, the harness still uses
  the graph but adds a `stale` flag to `diagnostics`. Re-indexing starts in the background from a
  `SessionStart` hook or when HEAD changes.
- Confirm in M0 that both engines respect `.gitignore`, so `node_modules`, build output and
  vendored code stay out of the index.

---

## 3. Retrieval pipeline (`rh retrieve`)

| Stage | What it does | Tools used |
|---|---|---|
| 0. Gate | Skips trivial prompts ("commit this", "thanks", very short messages) and returns empty output | Heuristics |
| 1. Task analysis | Uses regex to extract identifiers (CamelCase, snake_case), quoted strings, file paths, error messages and config keys. Flags cross-file wording such as "callers", "rename", "refactor" or "across". | No LLM |
| 2. Parallel seed search | Runs exact search on the extracted anchors and semantic search on the task text at the same time, each with its own timeout | `rg`, `colgrep` |
| 3. Normalize and fuse | Converts all results to `Evidence{path, start, end, source, score, symbol}`, merges overlapping ranges, removes duplicates and ranks with reciprocal rank fusion (RRF). Records which engine produced each result. | None |
| 4. Graph expansion *(conditional)* | Runs only if stage 1 found cross-file wording or the top results span several files. For the top-k symbols, calls `trace_path` in both directions at depth 1, and finds tests that reference those symbols. | codebase-memory-mcp |
| 5. Verify and excerpt | Re-reads each range from disk, drops stale or missing ranges, and expands each excerpt to its enclosing function where possible | None |
| 5a. Judge *(optional, off by default)* | A local Ollama model returns the excerpts it considers necessary; the rest are dropped before the budget is filled. Fails open. Not bounded by the 8 s deadline yet. | Ollama (`/api/chat`, JSON-schema output) |
| 6. Fit to budget | Fills the token budget in this priority order: primary code, then tests, then callers/callees, then docs. Anything that doesn't fit is listed as a path-only reference. | None |
| 7. Render | Writes `context.md` (summary, excerpts, tests, doc references, and a Mermaid diagram only when there are at least N graph edges) and `context.json` | None |

### Reliability rules

- If an engine is missing, slow or unindexed, the harness skips it, records the reason in
  `diagnostics`, and still returns a result. A hook must never block or break the agent.
- The whole retrieval has a hard deadline (default 8 s).
- Token counting uses chars/4. `tiktoken` is added only if this proves inaccurate.

---

## 4. Agent profiles

Profiles are defined in `src/rh/default_rh.toml (per-repo override: .rh/rh.toml)` and select the budget, engines and output format for
each agent.

```toml
[defaults]
deadline_s = 8
top_k = 8
engines = ["ripgrep", "colgrep", "cbm"]

[profile.claude-code]
budget_tokens = 6000

# for any agent running a small-context local model (e.g. Copilot + Ollama)
[profile.local-small]
budget_tokens = 2500        # local models have small context windows
deadline_s = 6
```

### Copilot (VS Code) with a local Ollama model

**Verified against the official docs on 2026-10-19** (VS Code hooks reference, GitHub Copilot hooks
reference, Ollama's VS Code page). This corrects the first version of this plan.

- **VS Code `UserPromptSubmit` cannot inject context.** The Local hooks reference documents only
  the common output (`continue`, `stopReason`, `systemMessage`) for this event. `additionalContext`
  is documented for `SessionStart`, `PreToolUse`, `PostToolUse` and `SubagentStart`.
- **Copilot config-file `userPromptSubmitted` hooks have their output dropped.**
- So for VS Code / Copilot the hook *writes* `.rh/context.md` and `.github/copilot-instructions.md`
  tells the agent to read it (`rh hook vscode`, `examples/vscode/`). Whether a local model follows
  that instruction is an M4 question, not an assumption. The MCP server is the alternative.
- Ollama models are used through the **Ollama VS Code extension** (VS Code 1.127+); they appear in
  the chat model picker. The `github.copilot.chat.byok.ollamaEndpoint` setting from the first
  draft could not be confirmed and is not used.
- Ollama's docs recommend a context length of **at least 64k** for local models (the first draft
  said 16k). Otherwise injected context can be cut off silently.
- Choose a model that supports tool calling, or it is left out of Agent mode.

**Claude Code** (verified): `UserPromptSubmit` stdout is added as context, and
`hookSpecificOutput.additionalContext` works, but is **capped at 10,000 characters** (longer text is
replaced by a 2,000-character preview). The `claude-code` profile therefore uses a 2,200-token budget.

---

## 5. Repo layout (Python + uv)

```
retrieval-harness/
  pyproject.toml              # entry point: rh
  rh/
    cli.py                    # rh init | doctor | retrieve | hook <agent> | mcp
    state.py                  # .rh/state.json, staleness checks
    analyze.py                # gate, anchor extraction, cross-file signal
    engines/
      base.py                 # Engine protocol: available(), search(query, deadline) -> [Evidence]
      ripgrep.py
      colgrep.py
      cbm.py                  # wrapper for the codebase-memory-mcp CLI
    fuse.py                   # dedupe, range merge, RRF
    expand.py                 # graph expansion policy
    pack.py                   # verify, excerpt, token budget
    render.py                 # md / json / mermaid
    models.py                 # Evidence, ContextPackage
    adapters/
      claude_code.py
      vscode.py
      mcp_server.py
  src/rh/default_rh.toml (per-repo override: .rh/rh.toml)
  eval/
    tasks/*.yaml              # task, repo@commit, gold files/tests
    run_retrieval.py          # recall@k, tokens, latency per engine combination
    run_agent.py              # baseline vs harness, end-to-end
  tests/
    fixtures/                 # recorded real outputs of rg / colgrep / cbm
```

---

## 6. Milestones

### M0 — Verify the tools (½ day)
- Install ColGREP and codebase-memory-mcp, and index one target repo with each.
- Record the real output of `rg --json`, `colgrep` and
  `codebase-memory-mcp cli ... --format json` as fixtures.
- Measure index build time and per-query latency.
- Check the Copilot + Ollama settings above, and that VS Code hooks are available.
- **Deliverable:** `rh doctor` and `rh init`.

### M1 — Core `rh retrieve` (1–2 days)
- Stages 0–7.
- Unit tests that run against the recorded fixtures, so they don't need the tools installed.

### M2 — Adapters (½ day each)
1. Claude Code `UserPromptSubmit` (stable).
2. VS Code / Copilot with the `local-small` profile.
3. MCP server (universal fallback).

### M3 — Retrieval evaluation (1 day, before any agent runs)
- Build tasks from real commits: the commit message or linked issue is the task, and the files
  and tests it changed are the gold set.
- Measure file recall@5/@10, test recall, tokens and latency for each configuration: `rg` only,
  `colgrep` only, `colgrep` + `rg`, all three with graph expansion.

### M4 — End-to-end evaluation (5+ tasks)
- Run each task with and without the harness, on both Claude Code and Copilot + Ollama.
- Record whether the task was solved (tests pass), the agent's tool calls and tokens, wall time
  and cost.

---

## 7. Open questions

1. Which agent do we target first? (Recommended: Claude Code, then Copilot + Ollama.)
2. Which repo do the evaluation tasks come from? It needs a good commit history, a test suite,
   and a language both engines support well.
3. Which Ollama model do we use, and how much VRAM is available?
4. Which Copilot target: the VS Code Local harness, the Copilot harness, or the Copilot CLI?
