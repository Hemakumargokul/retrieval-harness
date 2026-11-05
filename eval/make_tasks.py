"""Turn real commits into evaluation tasks: the commit message is the task, the files it changed
are the gold set (split into source files and test files).

    uv run python eval/make_tasks.py --repo /path/to/repo --n 20 --out eval/tasks
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Task, git, is_test  # noqa: E402

SOURCE_EXTS = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".rb", ".cs"}
SKIP_SUBJECT = re.compile(r"^(merge|revert|bump|release|version|update (deps|dependencies)|\[?pre-commit)", re.I)


def candidate_commits(repo: Path, scan: int) -> list[str]:
    return git(repo, "log", "--no-merges", f"-{scan}", "--format=%H").split()


def make_task(repo: Path, sha: str, min_files: int, max_files: int) -> Task | None:
    subject, body = (git(repo, "show", "-s", "--format=%s%x00%b", sha).split("\0") + [""])[:2]
    if SKIP_SUBJECT.match(subject) or len(subject.split()) < 4:
        return None
    parents = git(repo, "show", "-s", "--format=%P", sha).split()
    if len(parents) != 1:
        return None
    changed = git(repo, "diff", "--name-only", "--diff-filter=AM", parents[0], sha).split("\n")
    changed = [c for c in changed if c]
    src = [c for c in changed if Path(c).suffix in SOURCE_EXTS]
    gold_tests = [c for c in src if is_test(c)]
    gold_files = [c for c in src if not is_test(c)]
    # modified-only gold files: a brand-new file cannot be retrieved from the parent commit
    existing = set(git(repo, "ls-tree", "-r", "--name-only", parents[0]).split("\n"))
    gold_files = [f for f in gold_files if f in existing]
    if not gold_files or not (min_files <= len(changed) <= max_files):
        return None
    text = subject if not body.strip() else f"{subject}\n\n{body.strip()}"
    return Task(id=sha[:10], repo=str(repo), base_commit=parents[0], solution_commit=sha,
                task=text[:1500], gold_files=gold_files, gold_tests=[t for t in gold_tests if t in existing], solution_tests=gold_tests)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--scan", type=int, default=400, help="how many recent commits to look at")
    ap.add_argument("--min-files", type=int, default=2)
    ap.add_argument("--max-files", type=int, default=10)
    ap.add_argument("--out", default="eval/tasks")
    a = ap.parse_args()
    repo, out = Path(a.repo).resolve(), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for sha in candidate_commits(repo, a.scan):
        t = make_task(repo, sha, a.min_files, a.max_files)
        if t:
            print(f"{t.id}  {len(t.gold_files)} src, {len(t.gold_tests)} tests  {t.task.splitlines()[0][:70]}")
            t.save(out)
            n += 1
            if n >= a.n:
                break
    print(f"wrote {n} tasks to {out}; review a few by hand before trusting them")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
