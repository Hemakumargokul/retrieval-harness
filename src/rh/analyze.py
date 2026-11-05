from __future__ import annotations

import re
from dataclasses import dataclass, field

SKIP_PHRASES = {
    "thanks", "thank you", "thx", "ok", "okay", "yes", "no", "continue", "go on", "proceed",
    "commit", "commit this", "push", "push it", "looks good", "lgtm", "done", "stop",
}
CROSS_FILE_KEYWORDS = (
    "callers", "caller of", "all usages", "usages of", "rename", "refactor", "across",
    "everywhere", "who calls", "dependency", "dependencies", "call sites", "call graph",
)
KNOWN_EXTS = (
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "kt", "c", "h", "cc", "cpp", "hpp",
    "cs", "rb", "php", "swift", "scala", "toml", "yaml", "yml", "json", "md", "sh", "sql",
)
STOPWORDS = {
    "the", "this", "that", "with", "from", "into", "when", "what", "where", "which", "should",
    "would", "could", "function", "class", "method", "file", "files", "code", "error", "test",
    "tests", "add", "fix", "make", "use", "using", "update", "change", "please", "need", "want",
    "have", "does", "not", "all", "any", "and", "for", "are", "was", "but", "can", "how",
    "why", "none", "true", "false", "null",
}

_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b|\b[a-z]+(?:[A-Z][a-z0-9]+)+\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
_QUOTED = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|'([^'\n]+)'")
_PATH = re.compile(
    r"(?<![\w/.])((?:[\w.-]+/)+[\w.-]+|[\w-]+\.(?:" + "|".join(KNOWN_EXTS) + r"))\b"
)
_ERROR_LINE = re.compile(r"(Error|Exception|Traceback|panic:|FATAL)\b|^\s+File \".*\", line \d+")


@dataclass
class TaskAnalysis:
    identifiers: list[str] = field(default_factory=list)
    quoted: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cross_file: bool = False
    words: set[str] = field(default_factory=set)  # lowercase word tokens of the prompt
    semantic_query: str = ""

    def anchors(self, limit: int = 8) -> list[str]:
        """Exact-search anchors, most specific first, deduplicated."""
        seen: dict[str, None] = {}
        for group in (self.errors, self.paths, self.quoted, self.identifiers):
            for a in group:
                seen.setdefault(a, None)
        return list(seen)[:limit]


def should_skip(prompt: str) -> bool:
    p = prompt.strip()
    if not p or p.startswith("/"):
        return True
    if p.lower().rstrip(".!? ") in SKIP_PHRASES:
        return True
    return len(p.split()) < 4 and not (_PATH.search(p) or _CAMEL.search(p) or _SNAKE.search(p))


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def analyze(prompt: str) -> TaskAnalysis:
    lines = prompt.splitlines()
    error_lines = [l.strip() for l in lines if _ERROR_LINE.search(l)]
    errors = [l for l in error_lines if len(l) <= 200 and not l.startswith(("File ", "Traceback"))]

    quoted = [next(g for g in m.groups() if g) for m in _QUOTED.finditer(prompt)]
    quoted = [q for q in quoted if 2 < len(q) <= 80]

    paths = [m.group(1) for m in _PATH.finditer(prompt)]

    idents = _CAMEL.findall(prompt) + _SNAKE.findall(prompt)
    idents += [d for d in _DOTTED.findall(prompt) if d not in paths and not d.endswith(KNOWN_EXTS)]
    idents = [i for i in idents if i.lower() not in STOPWORDS and len(i) > 2]

    low = prompt.lower()
    body = "\n".join(l for l in lines if l.strip() not in set(error_lines))
    return TaskAnalysis(
        identifiers=_dedupe(idents),
        quoted=_dedupe(quoted),
        paths=_dedupe(paths),
        errors=_dedupe(errors),
        cross_file=any(k in low for k in CROSS_FILE_KEYWORDS),
        words={w for w in re.findall(r"\w+", low) if len(w) > 2},
        semantic_query=(body.strip() or prompt.strip())[:300],
    )
