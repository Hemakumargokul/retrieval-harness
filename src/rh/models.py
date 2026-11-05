from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Source = Literal["ripgrep", "colgrep", "cbm"]
Role = Literal["primary", "test", "caller", "callee", "doc"]
Level = Literal["info", "warn", "error"]

ROLE_PRIORITY: dict[str, int] = {"primary": 0, "test": 1, "caller": 2, "callee": 2, "doc": 3}


@dataclass
class Evidence:
    path: str
    start: int
    end: int
    source: str
    rank: int = 0
    score: float = 0.0
    symbol: str | None = None
    role: str = "primary"
    query: str = ""
    sources: list[str] = field(default_factory=list)  # every engine that found this range

    def __post_init__(self) -> None:
        if not self.sources:
            self.sources = [self.source]


@dataclass
class Edge:
    src: str
    dst: str
    kind: str  # "calls" | "imports"


@dataclass
class Diagnostic:
    engine: str
    level: str
    message: str


@dataclass
class ContextItem:
    path: str
    start: int
    end: int
    role: str
    text: str
    tokens: int
    sources: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class ContextPackage:
    task: str
    profile: str
    skipped: bool = False
    summary: str = ""
    items: list[ContextItem] = field(default_factory=list)
    references: list[Evidence] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)
    token_estimate: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ContextPackage:
        return cls(
            task=d["task"],
            profile=d["profile"],
            skipped=d.get("skipped", False),
            summary=d.get("summary", ""),
            items=[ContextItem(**i) for i in d.get("items", [])],
            references=[Evidence(**r) for r in d.get("references", [])],
            edges=[Edge(**e) for e in d.get("edges", [])],
            diagnostics=[Diagnostic(**x) for x in d.get("diagnostics", [])],
            timings_ms=dict(d.get("timings_ms", {})),
            token_estimate=d.get("token_estimate", 0),
        )
