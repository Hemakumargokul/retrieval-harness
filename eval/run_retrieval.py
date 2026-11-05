"""Retrieval-only evaluation (no agent): which engine combination finds the gold files?

    uv run python eval/run_retrieval.py --tasks eval/tasks --out eval/results
"""
from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Worktree, load_tasks  # noqa: E402
from metrics import mean, percentile, ranked_paths, recall_at  # noqa: E402

from rh.config import load_config  # noqa: E402
from rh.lifecycle import init  # noqa: E402
from rh.retrieve import retrieve  # noqa: E402

CONFIGS: dict[str, dict] = {
    "rg": {"engines": ["ripgrep"]},
    "colgrep": {"engines": ["colgrep"]},
    "colgrep+rg": {"engines": ["colgrep", "ripgrep"]},
    "colgrep+rg+cbm": {"engines": ["colgrep", "ripgrep", "cbm"]},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="eval/tasks")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--only", nargs="*", help="task ids")
    ap.add_argument("--configs", nargs="*", default=list(CONFIGS))
    ap.add_argument("--budget", type=int, default=20000, help="large, so recall is not budget-limited")
    ap.add_argument("--deadline", type=float, default=30, help="seconds; the hook default (8) would hide slow engines")
    a = ap.parse_args()

    tasks = load_tasks(Path(a.tasks), a.only)
    if not tasks:
        print("no tasks found", file=sys.stderr)
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in tasks:
            with Worktree(Path(t.repo), t.base_commit, Path(tmp), t.id) as wt:
                print(f"[{t.id}] indexing...", flush=True)
                t0 = time.monotonic()
                init(wt, log=lambda *_: None)
                index_s = time.monotonic() - t0
                for name in a.configs:
                    cfg = load_config(wt, None, budget_tokens=a.budget, deadline_s=a.deadline,
                                      per_engine_timeout_s=a.deadline, **CONFIGS[name])
                    pkg = retrieve(wt, t.task, cfg)
                    paths = ranked_paths(pkg)
                    rows.append({
                        "task": t.id, "config": name,
                        "file_recall@5": recall_at(paths, t.gold_files, 5),
                        "file_recall@10": recall_at(paths, t.gold_files, 10),
                        "test_recall@10": recall_at(paths, t.gold_tests, 10),
                        "tokens": pkg.token_estimate, "latency_ms": pkg.timings_ms.get("total", 0),
                        "n_gold": len(t.gold_files), "index_s": round(index_s, 1),
                        "diagnostics": "; ".join(f"{d.engine}:{d.message}" for d in pkg.diagnostics if d.level != "info"),
                    })
                    r = rows[-1]
                    print(f"  {name:<16} R@5={r['file_recall@5']} R@10={r['file_recall@10']} "
                          f"tok={r['tokens']} {r['latency_ms']}ms", flush=True)

    with open(out / "retrieval.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    lines = [f"# Retrieval evaluation ({len(tasks)} tasks)", "",
             "| config | file R@5 | file R@10 | test R@10 | tokens (mean) | latency p50 / p95 (ms) |",
             "|---|---|---|---|---|---|"]
    for name in a.configs:
        rs = [r for r in rows if r["config"] == name]
        lat = [r["latency_ms"] for r in rs]
        lines.append(f"| {name} | {mean([r['file_recall@5'] for r in rs]):.2f} | "
                     f"{mean([r['file_recall@10'] for r in rs]):.2f} | {mean([r['test_recall@10'] for r in rs]):.2f} | "
                     f"{mean([r['tokens'] for r in rs]):.0f} | {percentile(lat, .5):.0f} / {percentile(lat, .95):.0f} |")
    (out / "retrieval.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
