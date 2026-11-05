# VS Code / Copilot + local Ollama model

Verified against the VS Code and Ollama docs on 2026-10-19; items marked (unverified) were not.

1. Copy `.github/hooks/rh.json` and `.github/copilot-instructions.md` into your repo and replace
   `/ABS/PATH/TO/retrieval-harness`. Workspace hooks need Workspace Trust. `chat.useHooks` is on by default.
2. Install Ollama and the **Ollama extension** from the VS Code Marketplace (VS Code 1.127+).
   Models appear under *Ollama* in the chat model picker (endpoint default `http://127.0.0.1:11434`).
3. `ollama pull <a model that supports tool calling>`. A model without tool calling is left out of Agent mode.
4. Ollama allocates less context than VS Code displays. Ollama's docs recommend setting the
   context length to at least 64k in Ollama's settings, then reloading VS Code. Without that,
   the injected context can be silently cut off.
5. Run `rh init` once in the repo (starts the graph daemon), then `rh doctor`.

Why a file instead of injection: VS Code's `UserPromptSubmit` hook documents no way to add
context (only `continue`, `stopReason`, `systemMessage`), and Copilot config-file hooks drop
their output. So the hook writes `.rh/context.md` and the instructions file tells the agent to
read it. (unverified) Whether a given local model follows that instruction is what the M4 evaluation measures.
