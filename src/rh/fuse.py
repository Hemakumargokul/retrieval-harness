from __future__ import annotations

from rh.engines.base import EngineResult
from rh.models import Evidence

MERGE_GAP = 3


def fuse(results: list[EngineResult], k: int = 60) -> list[Evidence]:
    """Reciprocal rank fusion over all engines, then merge nearby ranges per file."""
    flat: list[Evidence] = []
    for r in results:
        for ev in r.evidence:
            flat.append(Evidence(**{**ev.__dict__, "score": 1.0 / (k + ev.rank + 1),
                                    "sources": list(ev.sources)}))

    by_file: dict[str, list[Evidence]] = {}
    for ev in flat:
        by_file.setdefault(ev.path, []).append(ev)

    merged: list[Evidence] = []
    for path, evs in by_file.items():
        evs.sort(key=lambda e: (e.start, e.end))
        cur = evs[0]
        for ev in evs[1:]:
            if ev.start <= cur.end + MERGE_GAP:
                cur.end = max(cur.end, ev.end)
                cur.score += ev.score
                cur.rank = min(cur.rank, ev.rank)
                cur.symbol = cur.symbol or ev.symbol
                if cur.role == "primary" and ev.role != "primary":
                    cur.role = ev.role
                for s in ev.sources:
                    if s not in cur.sources:
                        cur.sources.append(s)
            else:
                merged.append(cur)
                cur = ev
        merged.append(cur)

    merged.sort(key=lambda e: (-e.score, e.path, e.start))
    for i, ev in enumerate(merged):
        ev.rank = i
    return merged
