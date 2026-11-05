"""Pipeline trace: a snapshot of each meaningful stage, so a human can verify a run.

`Trace.record(stage, **data)` is called from retrieve(); everything stored is plain JSON data
(snapshotted at call time, so later mutation of Evidence objects cannot change it).
`write()` produces trace.json and a self-contained trace.html (see rh/trace_html.py).
Tracing is on by default: each run is written to <repo>/.rh/trace/<timestamp>/ (and copied to
.rh/trace/latest/); only the newest KEEP runs are kept. RH_TRACE_DIR moves the directory,
RH_TRACE=0 turns tracing off.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from rh.models import Evidence

STAGE_ORDER = ["gate", "analyze", "engines", "search", "fuse", "expand", "excerpt", "judge", "budget"]


def ev_dict(ev: Evidence) -> dict:
    return {"path": ev.path, "start": ev.start, "end": ev.end, "role": ev.role,
            "sources": list(ev.sources), "rank": ev.rank, "score": round(ev.score, 5),
            "symbol": ev.symbol, "query": ev.query}


class Trace:
    def __init__(self, enabled: bool = True) -> None:
        self.stages: dict[str, dict] = {}
        self.started = time.time()
        self.enabled = enabled

    @classmethod
    def disabled(cls) -> "Trace":
        """A trace that records nothing, so the pipeline needs no `if trace:` checks."""
        return cls(enabled=False)

    def record(self, stage: str, **data) -> None:
        if self.enabled:
            self.stages.setdefault(stage, {}).update(data)

    def to_dict(self, pkg=None, cfg=None, repo: Path | None = None) -> dict:
        out: dict = {"started": self.started, "repo": str(repo) if repo else None,
                     "stages": self.stages}
        if cfg is not None:
            out["config"] = asdict(cfg)
        if pkg is not None:
            from rh.render import to_markdown

            out["package"] = pkg.to_dict()
            out["output_markdown"] = to_markdown(pkg, cfg.mermaid_min_edges if cfg else 3)
        return out

    def write(self, directory: Path, pkg, cfg, repo: Path | None = None) -> Path:
        from rh.trace_html import render_html

        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        data = self.to_dict(pkg, cfg, repo)
        (d / "trace.json").write_text(json.dumps(data, indent=2, default=str))
        (d / "trace.html").write_text(render_html(data))
        return d / "trace.html"


KEEP = 20


def trace_enabled() -> bool:
    return os.environ.get("RH_TRACE", "1").lower() not in ("0", "off", "false", "no")


def trace_root(repo: Path) -> Path:
    return Path(os.environ["RH_TRACE_DIR"]) if os.environ.get("RH_TRACE_DIR") else Path(repo) / ".rh" / "trace"


def write_run(trace: Trace, pkg, cfg, repo: Path, root: Path | None = None) -> Path:
    """Write a run to <root>/<timestamp>/, refresh <root>/latest/ and prune old runs."""
    root = Path(root) if root else trace_root(repo)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(trace.started))
    run = root / stamp
    n = 1
    while run.exists():  # two runs in the same second
        n += 1
        run = root / f"{stamp}-{n}"
    html = trace.write(run, pkg, cfg, repo)
    latest = root / "latest"
    shutil.rmtree(latest, ignore_errors=True)
    shutil.copytree(run, latest)
    runs = sorted(d for d in root.iterdir() if d.is_dir() and d.name != "latest")
    for old in runs[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return html


def env_trace() -> Trace | None:
    """A Trace unless RH_TRACE=0 (used by the hook adapters)."""
    return Trace() if trace_enabled() else None


def finish_env_trace(trace: Trace | None, pkg, cfg, repo: Path) -> None:
    """Write the trace of a hook run. Never raises."""
    if trace is None:
        return
    try:
        write_run(trace, pkg, cfg, repo)
    except Exception:
        pass
