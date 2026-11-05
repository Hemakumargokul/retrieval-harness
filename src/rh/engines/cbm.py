from __future__ import annotations

import json
from pathlib import Path

from rh.engines._proc import find_binary, run
from rh.engines.base import EngineResult, EngineStatus, role_for_path
from rh.models import Diagnostic, Edge, Evidence
from rh.state import State, is_graph_stale

LABELS = "(f:Function OR f:Method OR f:Class)"


def _quote(name: str) -> str:
    return "'" + name.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rows(stdout: str) -> list[list[str]]:
    try:
        return json.loads(stdout).get("rows", [])
    except (ValueError, AttributeError):
        return []


def _real_path(path: str) -> bool:
    """The graph models builtins/externals as pseudo-files such as '<python-builtins>'."""
    return bool(path) and not path.startswith("<")


def parse_locate(stdout: str) -> list[Evidence]:
    out = []
    rows = [r[:4] for r in _rows(stdout) if len(r) >= 4 and _real_path(r[1]) and r[2].isdigit()]
    for i, (name, path, s, e) in enumerate(rows):
        out.append(Evidence(path=path, start=int(s), end=int(e or s), source="cbm", rank=i,
                            symbol=name, role=role_for_path(path)))
    return out


def parse_neighbors(stdout: str, direction: str) -> tuple[list[Evidence], list[Edge]]:
    """Rows are [focus_name, other_name, other_path, other_start, other_end]."""
    evs, edges = [], []
    rows = [r[:5] for r in _rows(stdout) if len(r) >= 5 and _real_path(r[2]) and r[3].isdigit()]
    for i, (focus, other, path, s, e) in enumerate(rows):
        role = role_for_path(path)
        role = role if role == "test" else direction
        evs.append(Evidence(path=path, start=int(s), end=int(e or s), source="cbm", rank=i,
                            symbol=other, role=role))
        edges.append(Edge(other, focus, "calls") if direction == "caller" else Edge(focus, other, "calls"))
    return evs, edges


class CbmEngine:
    """Graph expansion via `codebase-memory-mcp cli query_graph` (one Cypher call per direction)."""

    name = "cbm"
    seeds = False  # not a seed search engine: ColGREP/ripgrep seed, the graph expands

    def status(self, repo: Path, state: State) -> EngineStatus:
        if not find_binary("codebase-memory-mcp"):
            return EngineStatus.MISSING
        if not state.cbm_project:
            return EngineStatus.UNINDEXED
        return EngineStatus.STALE if is_graph_stale(state, repo) else EngineStatus.OK

    def _query(self, project: str, cypher: str, timeout_s: float) -> tuple[str, Diagnostic | None]:
        exe = find_binary("codebase-memory-mcp")
        if not exe:
            return "", Diagnostic("cbm", "warn", "codebase-memory-mcp not installed")
        rc, out, err, _ = run([exe, "cli", "--quiet", "query_graph", "--project", project,
                               "--format", "json", "--query", cypher], None, timeout_s)
        if rc != 0:
            return "", Diagnostic("cbm", "warn", f"rc={rc}: {(err or out).strip()[:120]}")
        return out, None

    def locate(self, project: str, symbols: list[str], timeout_s: float) -> EngineResult:
        names = ", ".join(_quote(s) for s in symbols)
        q = (f"MATCH (f) WHERE f.name IN [{names}] AND {LABELS} "
             "RETURN f.name, f.file_path, f.start_line, f.end_line LIMIT 40")
        out, diag = self._query(project, q, timeout_s)
        return EngineResult(parse_locate(out), [diag] if diag else [])

    def neighbors(self, project: str, symbols: list[str], direction: str, timeout_s: float
                  ) -> tuple[list[Evidence], list[Edge], list[Diagnostic]]:
        names = ", ".join(_quote(s) for s in symbols)
        pat = "(c)-[:CALLS]->(f)" if direction == "caller" else "(f)-[:CALLS]->(c)"
        q = (f"MATCH {pat} WHERE f.name IN [{names}] AND {LABELS} AND (c:Function OR c:Method) "
             "RETURN f.name, c.name, c.file_path, c.start_line, c.end_line LIMIT 40")
        out, diag = self._query(project, q, timeout_s)
        evs, edges = parse_neighbors(out, direction)
        return evs, edges, [diag] if diag else []

    def search(self, repo: Path, queries: list[str], timeout_s: float) -> EngineResult:
        from rh.state import load_state
        st = load_state(repo)
        if not st.cbm_project:
            return EngineResult(diagnostics=[Diagnostic("cbm", "warn", "no graph project; run rh init")])
        return self.locate(st.cbm_project, queries, timeout_s)
