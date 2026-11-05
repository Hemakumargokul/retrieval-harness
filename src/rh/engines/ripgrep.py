from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from rh.engines._proc import find_binary, run
from rh.engines.base import EngineResult, EngineStatus, role_for_path
from rh.models import Diagnostic, Evidence
from rh.state import State


def parse_rg_json(stdout: str, query: str) -> list[Evidence]:
    out: list[Evidence] = []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "match":
            continue
        d = ev["data"]
        path = d.get("path", {}).get("text")
        n = d.get("line_number")
        if path is None or n is None:
            continue
        path = path.removeprefix("./")
        out.append(Evidence(path=path, start=n, end=n, source="ripgrep",
                            role=role_for_path(path), query=query))
    return out


def rank_by_anchor_coverage(hits: list[Evidence]) -> list[Evidence]:
    """Files matched by more distinct anchors rank first; ties keep first-seen order."""
    anchors: dict[str, set[str]] = {}
    for h in hits:
        anchors.setdefault(h.path, set()).add(h.query)
    order = {p: i for i, p in enumerate(anchors)}
    ranked = sorted(hits, key=lambda h: (-len(anchors[h.path]), order[h.path], h.start))
    for i, h in enumerate(ranked):
        h.rank = i
    return ranked


def _looks_like_path(q: str) -> bool:
    return "/" in q or bool(re.fullmatch(r"[\w.-]+\.[A-Za-z0-9]{1,5}", q))


class RipgrepEngine:
    name = "ripgrep"

    def status(self, repo: Path, state: State) -> EngineStatus:
        return EngineStatus.OK if find_binary("rg") else EngineStatus.MISSING

    def search(self, repo: Path, queries: list[str], timeout_s: float) -> EngineResult:
        res = EngineResult()
        rg = find_binary("rg")
        if not rg:
            res.diagnostics.append(Diagnostic("ripgrep", "warn", "rg not installed"))
            return res
        hits: list[Evidence] = []
        for q in queries:
            if _looks_like_path(q):
                rc, out, _, ms = run([rg, "--files", "-g", f"*{q}"], repo, timeout_s)
                res.ms += ms
                if rc == 0:
                    for f in out.splitlines()[:5]:
                        f = f.removeprefix("./")
                        hits.append(Evidence(path=f, start=1, end=1, source="ripgrep",
                                             role=role_for_path(f), query=q))
            rc, out, err, ms = run(
                [rg, "--json", "-n", "-F", "--max-count", "20", "--", q, "."], repo, timeout_s
            )
            res.ms += ms
            if rc in (0, 1):  # 1 = no matches
                hits += parse_rg_json(out, q)
            else:
                res.diagnostics.append(Diagnostic("ripgrep", "warn", f"{q!r}: {err.strip()[:120]}"))
                if rc == -1:
                    break
        res.evidence = rank_by_anchor_coverage(hits)
        return res
