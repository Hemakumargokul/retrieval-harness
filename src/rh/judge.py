"""LLM-as-judge stage: ask a local Ollama model which excerpts are needed for the task.

Runs after excerpting and before the token budget is filled. It only ever *removes* excerpts,
and it fails open: if Ollama is unreachable, slow, or answers with something unusable, every
excerpt is kept and a warning diagnostic says why. It deliberately does not respect the
retrieval deadline (`deadline_s`); it has its own `judge_timeout_s`. Fitting it into the hook's
time budget is a later optimisation.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from rh.config import Config
from rh.models import ContextItem, Diagnostic

SYSTEM_PROMPT = (
    "You filter code context for a coding assistant. You are given a task and numbered code "
    "excerpts retrieved for it. Select the excerpts the assistant needs to do the task: the code "
    "to change, the code that calls or is called by it, and the tests that cover it. Exclude "
    "excerpts that are unrelated, even if they look similar. Answer only with JSON."
)
KEEP_SCHEMA = {
    "type": "object",
    "properties": {"keep": {"type": "array", "items": {"type": "integer"}}},
    "required": ["keep"],
}


class JudgeError(Exception):
    pass


def resolve_model(cfg: Config) -> str:
    return cfg.judge_model or os.environ.get("RH_JUDGE_MODEL", "")


def build_messages(task: str, items: list[ContextItem], snippet_lines: int) -> list[dict]:
    blocks = []
    for i, it in enumerate(items):
        lines = it.text.splitlines()
        snippet = "\n".join(lines[:snippet_lines])
        more = f"\n... ({len(lines) - snippet_lines} more lines)" if len(lines) > snippet_lines else ""
        blocks.append(f"[{i}] {it.role} {it.path}:{it.start}-{it.end}\n{snippet}{more}")
    user = (f"Task:\n{task.strip()[:1500]}\n\nExcerpts:\n\n" + "\n\n".join(blocks) +
            '\n\nReturn {"keep": [ids]} listing the excerpt numbers to keep.')
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def ollama_chat(cfg: Config, model: str, messages: list[dict]) -> str:
    """POST /api/chat with a JSON-schema `format`; returns the assistant message content."""
    body = json.dumps({
        "model": model, "messages": messages, "stream": False, "format": KEEP_SCHEMA,
        "think": False, "keep_alive": 0,  # no reasoning tokens; unload the model right after answering
        "options": {"temperature": 0, "num_ctx": cfg.judge_num_ctx},
    }).encode()
    req = urllib.request.Request(cfg.judge_endpoint.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.judge_timeout_s) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:150]
        raise JudgeError(f"ollama HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise JudgeError(f"ollama unreachable or timed out: {e}") from e
    except ValueError as e:
        raise JudgeError("ollama returned invalid JSON") from e
    content = (data.get("message") or {}).get("content")
    if not isinstance(content, str):
        raise JudgeError("ollama response has no message content")
    return content


DEFAULT_ENDPOINT = "http://127.0.0.1:11434"


def _alive(endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/api/version", timeout=1.5):
            return True
    except Exception:
        return False


@contextmanager
def ollama_session(cfg: Config, rec: dict):
    """Make sure Ollama is answering; if this call had to start it, stop it again on exit.
    Only auto-starts at the default local address, and only when the `ollama` binary exists."""
    proc = None
    if cfg.judge_autostart and cfg.judge_endpoint.rstrip("/") == DEFAULT_ENDPOINT and not _alive(cfg.judge_endpoint):
        exe = shutil.which("ollama")
        if exe:
            t0 = time.monotonic()
            try:
                proc = subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
                while time.monotonic() - t0 < 20 and proc.poll() is None and not _alive(cfg.judge_endpoint):
                    time.sleep(0.2)
                rec["ollama"] = f"started `ollama serve` in {time.monotonic() - t0:.1f}s; stopped afterwards"
            except OSError as e:
                proc = None
                rec["ollama"] = f"could not start ollama: {e}"
    elif cfg.judge_endpoint.rstrip("/") == DEFAULT_ENDPOINT and _alive(cfg.judge_endpoint):
        rec["ollama"] = "already running (left running; model unloaded after the call)"
    try:
        yield
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def parse_keep(content: str, n: int) -> list[int]:
    """Valid, de-duplicated indexes in 0..n-1 from the model's answer; [] if unusable."""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        try:
            data = json.loads(m.group(0)) if m else None
        except ValueError:
            data = None
    keep = data.get("keep") if isinstance(data, dict) else None
    if not isinstance(keep, list):
        return []
    out: list[int] = []
    for k in keep:
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n and idx not in out:
            out.append(idx)
    return out


_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class|function|func|fn|type|interface|struct|enum)\s+(?:\([^)]*\)\s*)?(\w+)"
                  r"|^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=", re.M)


def is_pinned(item: ContextItem, anchors: list[str]) -> bool:
    """Ripgrep matched this range exactly and it contains a definition whose name contains an
    anchor identifier (the function to change, or the test named after it). Imports, fixtures,
    docs and semantic-only neighbours are never pinned."""
    if "ripgrep" not in item.sources:
        return False
    names = [a.lower() for a in anchors if re.fullmatch(r"\w{3,}", a)]
    if not names:
        return False
    for m in _DEF.finditer(item.text):
        defined = (m.group(1) or m.group(2) or "").lower()
        if any(n in defined for n in names):
            return True
    return False


def judge(task: str, items: list[ContextItem], cfg: Config, record: dict | None = None,
          anchors: list[str] | None = None) -> tuple[list[ContextItem], list[Diagnostic], int]:
    """Return (items to keep, diagnostics, milliseconds). Never raises.
    If `record` is given it is filled with the decision, prompt and raw model answer (for traces)."""
    rec = record if record is not None else {}
    if not cfg.judge:
        rec["decision"] = "off"
        return items, [], 0
    all_items = items
    pinned_ids = {id(i) for i in items if is_pinned(i, anchors or [])}
    items = [i for i in all_items if id(i) not in pinned_ids]
    rec["pinned"] = [f"{i.path}:{i.start}-{i.end}" for i in all_items if id(i) in pinned_ids]
    if len(items) <= cfg.judge_min_items:
        rec["decision"] = f"skipped: {len(items)} judgeable excerpts, not more than judge_min_items={cfg.judge_min_items}"
        return all_items, [Diagnostic("judge", "info",
                                      f"skipped: {len(items)} judgeable excerpts, not more than {cfg.judge_min_items}")], 0
    model = resolve_model(cfg)
    if not model:
        rec["decision"] = "skipped: no model configured"
        return all_items, [Diagnostic("judge", "warn",
                                      "enabled but no model set (judge_model or RH_JUDGE_MODEL); kept all")], 0

    t0 = time.monotonic()
    try:
        messages = build_messages(task, items, cfg.judge_snippet_lines)
        rec.update(model=model, prompt=messages[1]["content"], system=messages[0]["content"])
        with ollama_session(cfg, rec):
            content = ollama_chat(cfg, model, messages)
        rec["raw"] = content
        keep = parse_keep(content, len(items))
        ms = int((time.monotonic() - t0) * 1000)
        if not keep:
            rec["decision"] = "ran, answer unusable: kept all"
            return all_items, [Diagnostic("judge", "warn", f"{model}: unusable answer, kept all")], ms
    except Exception as e:  # fail open: a broken judge must never remove or block context
        ms = int((time.monotonic() - t0) * 1000)
        rec.update(decision="ran, failed: kept all", error=str(e)[:300])
        return all_items, [Diagnostic("judge", "warn", f"{str(e)[:160]}; kept all")], ms

    rec.update(decision="ran", keep=sorted(keep), ms=ms)
    chosen = {id(items[i]) for i in keep}
    kept = [i for i in all_items if id(i) in pinned_ids or id(i) in chosen]
    msg = f"{model} kept {len(kept)} of {len(all_items)} ({len(pinned_ids)} pinned, {ms / 1000:.1f}s)"
    return kept, [Diagnostic("judge", "info", msg)], ms
