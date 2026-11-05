from __future__ import annotations

import ast
import math
from pathlib import Path

from rh.models import ROLE_PRIORITY, ContextItem, Evidence

CONTEXT_LINES = 6
SUMMARY_RESERVE = 0.10


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _read_lines(repo: Path, path: str) -> list[str] | None:
    try:
        return (Path(repo) / path).read_text(errors="replace").splitlines()
    except OSError:
        return None


def verify(ev: Evidence, repo: Path) -> Evidence | None:
    """Drop evidence whose file is gone or whose range is past the end of the file."""
    lines = _read_lines(repo, ev.path)
    if lines is None or ev.start < 1 or ev.start > len(lines):
        return None
    ev.end = min(ev.end, len(lines))
    return ev


def _enclosing_python_block(source: str, start: int, end: int) -> tuple[int, int] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            s = min([node.lineno] + [d.lineno for d in node.decorator_list])
            e = node.end_lineno or node.lineno
            if s <= start and end <= e and (best is None or (e - s) < (best[1] - best[0])):
                best = (s, e)
    return best


def excerpt(ev: Evidence, repo: Path, max_lines: int = 80) -> tuple[int, int, str]:
    lines = _read_lines(repo, ev.path) or []
    start, end = ev.start, ev.end
    if ev.path.endswith(".py"):
        block = _enclosing_python_block("\n".join(lines), start, end)
        if block:
            start, end = block
    if (start, end) == (ev.start, ev.end):  # no structural widening: use context lines
        start = max(1, start - CONTEXT_LINES)
        end = min(len(lines), end + CONTEXT_LINES)
    if end - start + 1 > max_lines:
        # keep the lines around the original hit
        start = max(start, min(ev.start, end - max_lines + 1))
        end = min(end, start + max_lines - 1)
    return start, end, "\n".join(lines[start - 1:end])


def build_items(evs: list[Evidence], repo: Path, max_lines: int) -> list[ContextItem]:
    items: list[ContextItem] = []
    for ev in evs:
        if verify(ev, repo) is None:
            continue
        s, e, text = excerpt(ev, repo, max_lines)
        items.append(ContextItem(path=ev.path, start=s, end=e, role=ev.role, text=text,
                                 tokens=estimate_tokens(text), sources=list(ev.sources),
                                 score=ev.score))
    return _dedupe_overlap(items)


def _dedupe_overlap(items: list[ContextItem]) -> list[ContextItem]:
    """Excerpts widened to the same function collapse into one."""
    kept: list[ContextItem] = []
    for it in items:  # items arrive best-first
        dup = next((k for k in kept if k.path == it.path and it.start <= k.end and k.start <= it.end), None)
        if dup is None:
            kept.append(it)
        else:
            for s in it.sources:
                if s not in dup.sources:
                    dup.sources.append(s)
    return kept


def pack(items: list[ContextItem], budget: int) -> tuple[list[ContextItem], list[ContextItem]]:
    """Greedy fill by role priority then score. Every input lands in exactly one list."""
    avail = int(budget * (1 - SUMMARY_RESERVE))
    ordered = sorted(items, key=lambda i: (ROLE_PRIORITY.get(i.role, 9), -i.score))
    kept: list[ContextItem] = []
    overflow: list[ContextItem] = []
    used = 0
    for it in ordered:
        if used + it.tokens <= avail:
            kept.append(it)
            used += it.tokens
        else:
            overflow.append(it)
    return kept, overflow
