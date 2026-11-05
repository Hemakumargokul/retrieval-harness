from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rh.engines.base import EngineStatus
from rh.engines._proc import find_binary, run
from rh.engines.cbm import CbmEngine
from rh.engines.colgrep import ColgrepEngine
from rh.engines.ripgrep import RipgrepEngine
from rh.state import State, git_head, is_graph_stale, load_state, save_state

LOCK_TTL_S = 30 * 60


@dataclass
class DoctorRow:
    engine: str
    status: str
    detail: str = ""


def _version(exe: str | None, flag: str = "--version") -> str:
    if not exe:
        return ""
    rc, out, _, _ = run([exe, flag], None, 20)
    return out.strip().splitlines()[0] if rc == 0 and out.strip() else ""


def _daemon_running() -> bool:
    exe = find_binary("codebase-memory-mcp")
    if not exe:
        return False
    rc, out, _, _ = run([exe, "daemon", "status"], None, 20)
    return rc == 0 and "active" in out


def doctor(repo: Path) -> list[DoctorRow]:
    state = load_state(repo)
    rows = []
    for eng, binary in ((RipgrepEngine(), "rg"), (ColgrepEngine(), "colgrep"), (CbmEngine(), "codebase-memory-mcp")):
        st = eng.status(repo, state)
        detail = _version(find_binary(binary))
        if eng.name == "cbm":
            if st != EngineStatus.MISSING and not _daemon_running():
                detail += "; daemon not running (calls take ~10s; run `rh init`)"
            if st == EngineStatus.STALE:
                detail += f"; built at {state.cbm_head and state.cbm_head[:8]}, HEAD is {(git_head(repo) or '?')[:8]}"
        if eng.name == "colgrep" and state.colgrep_model:
            detail += f"; model {state.colgrep_model}"
        rows.append(DoctorRow(eng.name, st.value, detail.strip("; ")))
    return rows


def init(repo: Path, log=print) -> State:
    state = load_state(repo)
    repo = Path(repo).resolve()

    colgrep = find_binary("colgrep")
    if colgrep:
        log("colgrep: indexing (the first run downloads a model)...")
        rc, out, err, ms = run([colgrep, "init", str(repo)], repo, 1800)
        if rc == 0:
            state.colgrep_built_at = time.time()
            _, status_out, _, _ = run([colgrep, "status", str(repo)], repo, 30)
            if m := re.search(r"^Model:\s*(\S+)", status_out, re.M):
                state.colgrep_model = m.group(1)
        log(f"colgrep: {'ok' if rc == 0 else 'FAILED ' + err.strip()[:200]} ({ms / 1000:.1f}s)")
    else:
        log("colgrep: not installed, skipped (see docs/tool-notes.md)")

    cbm = find_binary("codebase-memory-mcp")
    if cbm:
        log("cbm: starting daemon and indexing...")
        run([cbm, "daemon", "start"], None, 60)
        rc, out, err, ms = run([cbm, "cli", "--quiet", "index_repository", "--repo-path", str(repo)], repo, 1800)
        try:
            project = json.loads(out).get("project") if rc == 0 else None
        except ValueError:
            project = None
        if project:
            state.cbm_project, state.cbm_built_at, state.cbm_head = project, time.time(), git_head(repo)
        log(f"cbm: {'ok, project ' + project if project else 'FAILED ' + (err or out).strip()[:200]} ({ms / 1000:.1f}s)")
    else:
        log("cbm: not installed, skipped (uv sync --extra graph)")

    save_state(repo, state)
    return state


def reindex_in_background(repo: Path) -> bool:
    """Start `rh init` detached if the graph is stale. Returns True if a process was started."""
    repo = Path(repo).resolve()
    state = load_state(repo)
    if not (state.cbm_project and is_graph_stale(state, repo)):
        return False
    lock = repo / ".rh" / "reindex.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_TTL_S:
        return False
    lock.write_text(str(os.getpid()))
    script = ("import sys; from pathlib import Path; from rh.lifecycle import init; "
              "p = Path(sys.argv[1]);\n"
              "try: init(p, log=lambda *_: None)\n"
              "finally: (p / '.rh' / 'reindex.lock').unlink(missing_ok=True)")
    subprocess.Popen([sys.executable, "-c", script, str(repo)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True
