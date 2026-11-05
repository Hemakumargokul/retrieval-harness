from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from rh.models import Diagnostic, Evidence
from rh.state import State


class EngineStatus(Enum):
    MISSING = "missing"
    UNINDEXED = "unindexed"
    STALE = "stale"
    OK = "ok"


@dataclass
class EngineResult:
    evidence: list[Evidence] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    ms: int = 0


class Engine(Protocol):
    name: str

    def status(self, repo: Path, state: State) -> EngineStatus: ...

    def search(self, repo: Path, queries: list[str], timeout_s: float) -> EngineResult: ...


TEST_PATTERNS = ("test_", "_test.", ".spec.", ".test.")
TEST_DIRS = ("tests", "test", "__tests__", "spec")


def role_for_path(path: str) -> str:
    p = Path(path)
    if p.suffix in (".md", ".rst", ".txt"):
        return "doc"
    if any(part in TEST_DIRS for part in p.parts[:-1]) or any(t in p.name for t in TEST_PATTERNS):
        return "test"
    return "primary"
