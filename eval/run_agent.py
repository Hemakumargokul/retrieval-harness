"""End-to-end evaluation: does the agent solve the task better with the harness than without?

For each task and condition, check out the commit *before* the real fix, run the agent headless,
then copy in the tests from the real fix and run them. This costs real money, so nothing runs
without --execute.

    uv run python eval/run_agent.py --tasks eval/tasks --test-cmd "pytest -q" --execute --max-budget-usd 1.5

Claude Code only (headless via `claude -p`). For Copilot + Ollama, run the same two conditions by
hand and record the same columns. Set RH_AGENT_CMD to a script to replace the agent (used by tests).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Task, Worktree, git, load_tasks  # noqa: E402

from rh.lifecycle import init  # noqa: E402

HARNESS_ROOT = Path(__file__).resolve().parents[1]


def hook_settings(path: Path) -> Path:
    cmd = f"uv run --project {shlex.quote(str(HARNESS_ROOT))} rh hook claude-code"
    path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": cmd, "timeout": 30}]}]}}))
    return path


def agent_command(task: Task, condition: str, settings_file: Path | None, budget: float) -> list[str]:
    if override := os.environ.get("RH_AGENT_CMD"):
        return shlex.split(override) + [task.task, condition]
    cmd = ["claude", "-p", task.task, "--output-format", "json", "--permission-mode", "acceptEdits",
           "--no-session-persistence", "--setting-sources", "project,local",
           "--max-budget-usd", str(budget)]
    if condition == "harness" and settings_file:
        cmd += ["--settings", str(settings_file)]
    return cmd


def parse_agent_json(stdout: str) -> dict:
    try:
        d = json.loads(stdout)
    except ValueError:
        return {}
    u = d.get("usage") or {}
    return {"cost_usd": d.get("total_cost_usd"), "turns": d.get("num_turns"),
            "agent_ms": d.get("duration_ms"), "is_error": d.get("is_error"),
            "tokens_in": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                         + (u.get("cache_creation_input_tokens") or 0),
            "tokens_out": u.get("output_tokens")}


def apply_solution_tests(wt: Path, task: Task) -> None:
    for t in task.solution_tests:
        target = wt / t
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(git(Path(task.repo), "show", f"{task.solution_commit}:{t}"))


def run_one(task: Task, condition: str, a: argparse.Namespace, tmp: Path) -> dict:
    row = {"task": task.id, "condition": condition}
    with Worktree(Path(task.repo), task.base_commit, tmp, f"{task.id}-{condition}") as wt:
        settings = None
        if condition == "harness":
            init(wt, log=lambda *_: None)  # indexing time is reported separately, not charged to the agent
            settings = hook_settings(tmp / f"{task.id}-settings.json")
        t0 = time.monotonic()
        p = subprocess.run(agent_command(task, condition, settings, a.max_budget_usd), cwd=wt,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=a.timeout)
        row["wall_s"] = round(time.monotonic() - t0, 1)
        row.update(parse_agent_json(p.stdout))
        row["agent_rc"] = p.returncode
        changed = [c for c in git(wt, "diff", "--name-only", "HEAD").split("\n") if c]
        row["touched_gold"] = len(set(changed) & set(task.gold_files))
        row["n_gold"] = len(task.gold_files)
        if task.solution_tests:
            apply_solution_tests(wt, task)
            r = subprocess.run(shlex.split(a.test_cmd) + task.solution_tests, cwd=wt, capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=a.test_timeout)
            row["tests_pass"] = r.returncode == 0
        else:
            row["tests_pass"] = None
    return row


def summarize(rows: list[dict]) -> str:
    def avg(rs, k):
        vals = [r[k] for r in rs if isinstance(r.get(k), (int, float))]
        return f"{sum(vals) / len(vals):.2f}" if vals else "n/a"

    lines = [f"# Agent evaluation ({len({r['task'] for r in rows})} tasks)", "",
             "| condition | solved | touched gold files | wall s | cost USD | turns | tokens in | tokens out |",
             "|---|---|---|---|---|---|---|---|"]
    for cond in sorted({r["condition"] for r in rows}):
        rs = [r for r in rows if r["condition"] == cond]
        solved = sum(1 for r in rs if r["tests_pass"])
        touched = sum(r["touched_gold"] for r in rs)
        total = sum(r["n_gold"] for r in rs)
        lines.append(f"| {cond} | {solved}/{len(rs)} | {touched}/{total} | {avg(rs, 'wall_s')} | "
                     f"{avg(rs, 'cost_usd')} | {avg(rs, 'turns')} | {avg(rs, 'tokens_in')} | {avg(rs, 'tokens_out')} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--tasks", default="eval/tasks")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--conditions", nargs="*", default=["baseline", "harness"])
    ap.add_argument("--test-cmd", required=True, help='e.g. "pytest -q"; the solution test files are appended')
    ap.add_argument("--max-budget-usd", type=float, default=1.5, help="per run")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--test-timeout", type=int, default=600)
    ap.add_argument("--execute", action="store_true", help="actually run the agent (costs money)")
    a = ap.parse_args()

    tasks = load_tasks(Path(a.tasks), a.only)
    if not tasks:
        print("no tasks found", file=sys.stderr)
        return 1
    n = len(tasks) * len(a.conditions)
    if not a.execute:
        print(f"dry run: {n} agent runs ({len(tasks)} tasks x {a.conditions}), up to "
              f"${n * a.max_budget_usd:.2f} at --max-budget-usd {a.max_budget_usd}. Pass --execute to run.")
        for t in tasks:
            print(f"  {t.id}: {t.task.splitlines()[0][:80]}")
        return 0

    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in tasks:
            for cond in a.conditions:
                print(f"[{t.id}] {cond} ...", flush=True)
                try:
                    rows.append(run_one(t, cond, a, Path(tmp)))
                except Exception as e:  # keep going: one broken run must not lose the others
                    rows.append({"task": t.id, "condition": cond, "tests_pass": False, "touched_gold": 0,
                                 "n_gold": len(t.gold_files), "error": f"{type(e).__name__}: {e}"[:200]})
                print("   ", {k: v for k, v in rows[-1].items() if k not in ("task", "condition")}, flush=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(out / "agent.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    md = summarize(rows)
    (out / "agent.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
