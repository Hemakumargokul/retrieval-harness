from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rh.analyze import TaskAnalysis
from rh.config import Config
from rh.engines.cbm import CbmEngine
from rh.models import Diagnostic, Edge, Evidence

MAX_SYMBOLS = 6
MAX_NEW = 12
MIN_SECONDS = 4.5  # one graph round trip takes ~3.6 s with the daemon warm
TOP_N = 5


def expand_reason(analysis: TaskAnalysis, fused: list[Evidence]) -> str | None:
    """Why graph expansion should run, or None if it should not."""
    if not fused:
        return None
    if analysis.cross_file:
        return "the prompt has cross-file wording"
    spread = len({e.path for e in fused[:TOP_N]})
    if spread >= 3:
        return f"the top {TOP_N} results span {spread} files"
    return None


def should_expand(analysis: TaskAnalysis, fused: list[Evidence]) -> bool:
    return expand_reason(analysis, fused) is not None


def enclosing_symbol(ev: Evidence, repo: Path) -> str | None:
    if ev.symbol:
        return ev.symbol
    if not ev.path.endswith(".py"):
        return None
    try:
        tree = ast.parse((Path(repo) / ev.path).read_text(errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if n.lineno <= ev.start <= (n.end_lineno or n.lineno):
                if best is None or n.lineno > best.lineno:
                    best = n
    return best.name if best else None


def pick_symbols(fused: list[Evidence], analysis: TaskAnalysis, repo: Path) -> list[str]:
    """Symbols the prompt names come first. Only when it names none do we fall back to the
    symbols of the top hits, because widely used names (e.g. a core class) flood the callers."""
    candidates = [s for ev in fused[:TOP_N] if ev.role == "primary" and (s := enclosing_symbol(ev, repo))]
    named = [s for s in candidates if s.lower() in analysis.words]
    named += [i.rsplit(".", 1)[-1] for i in analysis.identifiers[:3]]
    chosen = list(dict.fromkeys(named))
    if not chosen:
        chosen = list(dict.fromkeys(candidates))
    return chosen[:MAX_SYMBOLS]


def expand(fused: list[Evidence], analysis: TaskAnalysis, cbm: CbmEngine, project: str,
           repo: Path, cfg: Config, timeout_s: float
           ) -> tuple[list[Evidence], list[Edge], list[Diagnostic]]:
    symbols = pick_symbols(fused, analysis, repo)
    if not symbols:
        return [], [], [Diagnostic("cbm", "info", "no symbols to expand")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(cbm.neighbors, project, symbols, d, timeout_s) for d in ("caller", "callee")]
        results = [f.result() for f in futs]
    known = {(e.path, e.start) for e in fused}
    evs: list[Evidence] = []
    edges: list[Edge] = []
    diags: list[Diagnostic] = []
    for r_evs, r_edges, r_diags in results:
        diags += r_diags
        edges += r_edges
        for ev in r_evs:
            if (ev.path, ev.start) not in known and len(evs) < MAX_NEW:
                known.add((ev.path, ev.start))
                evs.append(ev)
    base = len(fused)
    for i, ev in enumerate(evs):
        ev.rank = base + i
        ev.score = 1.0 / (cfg.rrf_k + ev.rank + 1)
    unique = [Edge(*t) for t in dict.fromkeys((e.src, e.dst, e.kind) for e in edges)]
    return evs, unique, diags
