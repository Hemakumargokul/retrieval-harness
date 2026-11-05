from __future__ import annotations

import json
from pathlib import Path

from rh.engines._proc import find_binary, run
from rh.engines.base import EngineResult, EngineStatus, role_for_path
from rh.models import Diagnostic, Evidence
from rh.state import State


def parse_colgrep_json(stdout: str, repo: Path, query: str = "", per_file: int = 2) -> list[Evidence]:
    """ColGREP's `unit` is a whole-file wrapper; the real best sub-units are in `matches`
    (already sorted by score). Emit the top few per file, falling back to the unit range."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return []
    out: list[Evidence] = []
    root = str(Path(repo).resolve())
    for hit in data if isinstance(data, list) else []:
        u = hit.get("unit") or {}
        f = u.get("file")
        if not f or u.get("line") is None:
            continue
        path = f[len(root) + 1:] if f.startswith(root + "/") else f.removeprefix("./")
        role = role_for_path(path)
        matches = [m for m in hit.get("matches") or [] if m.get("name") and m.get("line")]
        best = matches[0]["score"] if matches else 0
        picked = [m for m in matches if m["score"] >= 0.5 * best][:per_file]
        if not picked:
            picked = [{"line": u["line"], "end_line": u.get("end_line") or u["line"],
                       "name": u.get("name"), "score": hit.get("score") or 0}]
        for m in picked:
            out.append(Evidence(path=path, start=int(m["line"]), end=int(m.get("end_line") or m["line"]),
                                source="colgrep", rank=len(out), score=float(m.get("score") or 0),
                                symbol=m.get("name") or None, role=role, query=query))
    return out


class ColgrepEngine:
    name = "colgrep"
    seeds = True

    def __init__(self, k: int = 10, update: bool = False):
        # update=False passes --no-update: the per-prompt hook must never build an index
        self.k, self.update = k, update

    def status(self, repo: Path, state: State) -> EngineStatus:
        if not find_binary("colgrep"):
            return EngineStatus.MISSING
        return EngineStatus.OK if state.colgrep_built_at else EngineStatus.UNINDEXED

    def search(self, repo: Path, queries: list[str], timeout_s: float) -> EngineResult:
        res = EngineResult()
        exe = find_binary("colgrep")
        if not exe:
            res.diagnostics.append(Diagnostic("colgrep", "warn", "colgrep not installed"))
            return res
        q = queries[0]
        # --semantic-only turns off ColGREP's FTS5 keyword half (default is hybrid, alpha 0.60); exact search is ripgrep
        cmd = [exe, "--json", "--semantic-only", "-k", str(self.k), q, "."]
        if not self.update:
            cmd.insert(1, "--no-update")
        rc, out, err, res.ms = run(cmd, repo, timeout_s)
        if rc != 0:
            res.diagnostics.append(Diagnostic("colgrep", "warn", f"rc={rc}: {err.strip()[:120]}"))
            return res
        res.evidence = parse_colgrep_json(out, repo, q)
        return res
