from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

TEST_MARKERS = ("test_", "_test.", ".spec.", ".test.")
TEST_DIRS = {"tests", "test", "__tests__", "spec"}


def is_test(path: str) -> bool:
    p = Path(path)
    return any(part in TEST_DIRS for part in p.parts[:-1]) or any(m in p.name for m in TEST_MARKERS)


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


@dataclass
class Task:
    id: str
    repo: str
    base_commit: str       # the parent of the solution commit: what the agent starts from
    solution_commit: str
    task: str
    gold_files: list[str] = field(default_factory=list)
    gold_tests: list[str] = field(default_factory=list)      # existing at base_commit: retrievable
    solution_tests: list[str] = field(default_factory=list)  # all tests the solution touched (run after the agent)

    @classmethod
    def load(cls, path: Path) -> Task:
        d = json.loads(Path(path).read_text())
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, directory: Path) -> Path:
        out = Path(directory) / f"{self.id}.json"
        out.write_text(json.dumps(self.__dict__, indent=2))
        return out


def load_tasks(directory: Path, only: list[str] | None = None) -> list[Task]:
    tasks = [Task.load(p) for p in sorted(Path(directory).glob("*.json"))]
    return [t for t in tasks if not only or t.id in only]


class Worktree:
    """A detached checkout of `commit` in a temp dir, removed on exit."""

    def __init__(self, repo: Path, commit: str, root: Path, name: str):
        self.repo, self.commit, self.path = Path(repo), commit, Path(root) / name

    def __enter__(self) -> Path:
        git(self.repo, "worktree", "add", "--detach", "--force", str(self.path), self.commit)
        return self.path

    def __exit__(self, *exc) -> None:
        git(self.repo, "worktree", "remove", "--force", str(self.path), check=False)
