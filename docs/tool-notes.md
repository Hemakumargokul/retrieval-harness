# Tool notes (recorded 2026-10-19 against this repo, 25 files)

## Installation (no system changes)
| Tool | How installed | Why |
|---|---|---|
| ripgrep | `ripgrep` PyPI wheel in the dev group | no system `rg` binary on this machine (the `rg` in the Claude Code shell is a function) |
| codebase-memory-mcp 0.12.1 | optional extra `graph` (`uv sync --extra graph`) | on PyPI; the wheel downloads and checksum-verifies the native binary on first run |
| colgrep 1.8.5 | prebuilt release tarball (sha256 verified) into `.tools/bin/` | **not on PyPI/npm**; only crates.io (needs Rust) or release binaries |

Binary lookup order (`rh.engines._proc.find_binary`): `$RH_<NAME>_BIN`, PATH, the venv `bin/`, `<project>/.tools/bin/`.

## ColGREP
- `colgrep init <repo>`: 43 s for 25 files the first time (includes model download). Model `lightonai/LateOn-Code-edge`. Index lives under `~/.local/share/colgrep/indices/<name>-<hash>`.
- Query: 0.6 s warm. The first query after changes re-indexes first (3.7 s here). `--no-update` skips that.
- `--json` exists (the README doesn't mention it): a list of `{unit:{file(abs), line, end_line, name, unit_type, signature, ...}, score, matches:[...]}`. Fixture: `tests/fixtures/colgrep_search.json`.
- Plain output is `path:start-end  name`.
- Agent mode (`--agent`, ~2.5 GB model) is not used: the harness uses `--json` semantic search only.
- ONNX runtime reports GPU/CUDA availability on this machine.

## codebase-memory-mcp
- Project name is derived from the path (`home-hem-projects-retrieval-harness`) and returned by `index_repository`.
- Indexing: 14.6 s here (284 nodes, 913 edges). Respects `.venv`, `.git`, etc.
- **Every CLI call spawns a temporary daemon: ~10.1 s per call.** With `codebase-memory-mcp daemon start` (permanent) each call is ~3.6 s. So the graph is only usable inside a hook with the daemon running, and only ~1 round trip fits in the deadline. `rh init` starts the daemon; `rh doctor` reports it.
- Starting the daemon also enables a local HTTP UI (`ui_enabled=true`, port 9749). Disable with `codebase-memory-mcp config set ui_enabled false` if unwanted.
- `trace_path --format json` returns names + hop counts only (no file/line), so we use **one Cypher `query_graph` call** instead, which returns `name, file_path, start_line, end_line`. Cypher string literals in `RETURN` are not supported. Fixtures: `cbm_query_callers.json`, `cbm_query_callees.json`, `cbm_query_locate.json`.
- Values in `rows` are strings.

## Still unverified
- Claude Code `UserPromptSubmit` output contract was written from memory (`hookSpecificOutput.additionalContext`).
- VS Code / Copilot hook event names and payloads.
- Ollama + Copilot settings.
