from __future__ import annotations

import statistics

from rh.models import ContextPackage


def ranked_paths(pkg: ContextPackage) -> list[str]:
    """Unique paths from everything retrieved (included or overflowed), best score first."""
    scored = [(i.score, i.path) for i in pkg.items] + [(r.score, r.path) for r in pkg.references]
    scored.sort(key=lambda t: -t[0])
    return list(dict.fromkeys(p for _, p in scored))


def recall_at(paths: list[str], gold: list[str], k: int) -> float | None:
    if not gold:
        return None
    return len(set(paths[:k]) & set(gold)) / len(gold)


def mean(xs: list[float | None]) -> float:
    vals = [x for x in xs if x is not None]
    return statistics.fmean(vals) if vals else float("nan")


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]
