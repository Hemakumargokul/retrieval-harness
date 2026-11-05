from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from rh.analyze import analyze, should_skip
from rh.config import Config
from rh.engines import get_engines
from rh.engines.base import Engine, EngineResult
from rh.engines.cbm import CbmEngine
from rh.expand import MIN_SECONDS, expand, expand_reason, pick_symbols
from rh.fuse import fuse
from rh.judge import judge
from rh.models import ContextPackage, Diagnostic
from rh.pack import build_items, estimate_tokens, pack
from rh.render import make_summary
from rh.state import load_state
from rh.trace import Trace, ev_dict


def _queries_for(engine: Engine, anchors: list[str], semantic: str) -> list[str]:
    return anchors if engine.name == "ripgrep" else [semantic]


def retrieve(repo: Path, task: str, cfg: Config, engines: list[Engine] | None = None,
             trace: Trace | None = None) -> ContextPackage:
    t0 = time.monotonic()
    pkg = ContextPackage(task=task, profile=cfg.profile)
    try:
        return _retrieve(Path(repo), task, cfg, engines, pkg, t0, trace or Trace.disabled())
    except Exception as e:  # a hook must never break the agent
        pkg.diagnostics.append(Diagnostic("rh", "error", f"{type(e).__name__}: {e}"))
        return pkg


def _retrieve(repo: Path, task: str, cfg: Config, engines: list[Engine] | None,
              pkg: ContextPackage, t0: float, trace: Trace) -> ContextPackage:
    def mark(stage: str, since: float) -> None:
        pkg.timings_ms[stage] = int((time.monotonic() - since) * 1000)

    def remaining() -> float:
        return cfg.deadline_s - (time.monotonic() - t0)

    skip = should_skip(task)
    trace.record("gate", task=task, skipped=skip)
    if skip:
        pkg.skipped = True
        return pkg

    ts = time.monotonic()
    analysis = analyze(task)
    anchors = analysis.anchors()
    mark("analyze", ts)
    trace.record("analyze", identifiers=analysis.identifiers, quoted=analysis.quoted,
                 paths=analysis.paths, errors=analysis.errors, anchors=anchors,
                 cross_file=analysis.cross_file, semantic_query=analysis.semantic_query)

    state = load_state(repo)
    engines = engines if engines is not None else get_engines(cfg)
    runnable: list[Engine] = []
    graph: Engine | None = None
    engine_rows: list[dict] = []
    for eng in engines:
        st = eng.status(repo, state)
        engine_rows.append({"name": eng.name, "status": st.value,
                            "role": "seed search" if getattr(eng, "seeds", True) else "graph expansion"})
        if st.value in ("missing", "unindexed"):
            pkg.diagnostics.append(Diagnostic(eng.name, "warn", f"skipped: {st.value}"))
        else:
            if st.value == "stale":
                pkg.diagnostics.append(Diagnostic(eng.name, "warn", "index is stale"))
            if getattr(eng, "seeds", True):
                runnable.append(eng)
            else:
                graph = eng

    trace.record("engines", engines=engine_rows)

    ts = time.monotonic()
    results: list[EngineResult] = []
    by_engine: dict[str, EngineResult] = {}
    timed_out: list[str] = []
    jobs = [(e, _queries_for(e, anchors, analysis.semantic_query)) for e in runnable]
    jobs = [(e, q) for e, q in jobs if any(q)]
    if jobs:
        pool = ThreadPoolExecutor(max_workers=len(jobs))
        try:
            timeout = max(0.1, min(cfg.per_engine_timeout_s, remaining()))
            futs = {pool.submit(e.search, repo, q, timeout): e for e, q in jobs}
            done, pending = wait(futs, timeout=max(0.1, remaining()))
            for f in done:
                try:
                    results.append(f.result())
                    by_engine[futs[f].name] = results[-1]
                except Exception as ex:
                    pkg.diagnostics.append(Diagnostic(futs[f].name, "error", str(ex)))
            for f in pending:
                timed_out.append(futs[f].name)
                pkg.diagnostics.append(Diagnostic(futs[f].name, "warn", "deadline exceeded"))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    for r in results:
        pkg.diagnostics += r.diagnostics
    mark("search", ts)
    trace.record("search", engines=[
        {"name": e.name, "queries": q, "ms": by_engine[e.name].ms if e.name in by_engine else None,
         "timed_out": e.name in timed_out,
         "hits": [ev_dict(x) for x in by_engine[e.name].evidence] if e.name in by_engine else []}
        for e, q in jobs])

    ts = time.monotonic()
    fused = fuse(results, cfg.rrf_k)
    mark("fuse", ts)
    trace.record("fuse", total=len(fused), ranges=[ev_dict(x) for x in fused[:40]])

    edges = []
    reason = expand_reason(analysis, fused)
    if not isinstance(graph, CbmEngine):
        trace.record("expand", decision="not run: no graph engine available", symbols=[], new=[], edges=[])
    elif reason is None:
        trace.record("expand", decision="not run: no cross-file wording, and the top results are in fewer than 3 files",
                     symbols=[], new=[], edges=[])
    elif remaining() < MIN_SECONDS:
        pkg.diagnostics.append(Diagnostic("cbm", "info", "skipped expansion: not enough time left"))
        trace.record("expand", decision=f"skipped: not enough time left (needs {MIN_SECONDS}s)",
                     reason=reason, symbols=[], new=[], edges=[])
    else:
        ts = time.monotonic()
        project = state.cbm_project or ""
        symbols = pick_symbols(fused, analysis, repo)
        before = len(fused)
        new, edges, diags = expand(fused, analysis, graph, project, repo, cfg,
                                   max(0.5, remaining() - 0.3))
        fused += new
        pkg.diagnostics += diags
        mark("expand", ts)
        trace.record("expand", decision="ran", reason=reason, symbols=symbols,
                     new=[ev_dict(x) for x in new], edges=[[e.src, e.dst, e.kind] for e in edges],
                     fused_before=before)

    ts = time.monotonic()
    candidates = fused[: cfg.top_k * 3]
    trace.record("excerpt", candidates=[ev_dict(x) for x in candidates])
    items = build_items(candidates, repo, cfg.max_excerpt_lines)
    mark("excerpt", ts)
    trace.record("excerpt", items=[_item_dict(i) for i in items])

    if cfg.judge:
        ts = time.monotonic()
        before_judge = items
        rec: dict = {}
        items, jdiags, _ = judge(task, items, cfg, record=rec, anchors=anchors)
        pkg.diagnostics += jdiags
        mark("judge", ts)
        kept_ids = {id(i) for i in items}
        rec["kept"] = [_item_dict(i) for i in before_judge if id(i) in kept_ids]
        rec["dropped"] = [_item_dict(i) for i in before_judge if id(i) not in kept_ids]
        trace.record("judge", **rec)
    else:
        trace.record("judge", decision="off")

    ts = time.monotonic()
    kept, overflow = pack(items, cfg.budget_tokens)
    mark("pack", ts)
    trace.record("budget", budget=cfg.budget_tokens, usable=int(cfg.budget_tokens * 0.9),
                 used=sum(i.tokens for i in kept), kept=[_item_dict(i) for i in kept],
                 overflow=[_item_dict(i) for i in overflow])

    pkg.items = kept
    pkg.edges = edges
    pkg.references = [
        _as_reference(i) for i in overflow
    ] + [e for e in fused[cfg.top_k * 3:]]
    pkg.token_estimate = sum(i.tokens for i in kept)
    pkg.summary = make_summary(pkg)
    pkg.token_estimate += estimate_tokens(pkg.summary)
    pkg.timings_ms["total"] = int((time.monotonic() - t0) * 1000)
    return pkg


def _item_dict(i) -> dict:
    return {"path": i.path, "start": i.start, "end": i.end, "role": i.role, "tokens": i.tokens,
            "sources": list(i.sources), "score": round(i.score, 5)}


def _as_reference(item):
    from rh.models import Evidence

    return Evidence(path=item.path, start=item.start, end=item.end, source=item.sources[0],
                    role=item.role, score=item.score, sources=list(item.sources))
