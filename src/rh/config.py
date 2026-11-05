from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from importlib import resources
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass
class Config:
    profile: str = "default"
    deadline_s: float = 8
    per_engine_timeout_s: float = 5
    top_k: int = 8
    budget_tokens: int = 4000
    engines: list[str] = field(default_factory=lambda: ["ripgrep", "colgrep", "cbm"])
    colgrep_update: bool = False  # let colgrep refresh its index before each search (can take 10s+)
    mermaid_min_edges: int = 3
    max_excerpt_lines: int = 80
    rrf_k: int = 60
    # LLM-as-judge stage (local Ollama); see rh/judge.py. The packaged default turns it on.
    judge: bool = False
    judge_model: str = ""          # or set RH_JUDGE_MODEL
    judge_endpoint: str = "http://127.0.0.1:11434"
    judge_timeout_s: float = 60
    judge_num_ctx: int = 8192
    judge_min_items: int = 3       # only judge when there are more excerpts than this
    judge_snippet_lines: int = 15  # lines of each excerpt shown to the judge
    judge_autostart: bool = True   # start `ollama serve` if it is not running, stop it afterwards


def _read_default() -> dict:
    text = resources.files("rh").joinpath("default_rh.toml").read_text()
    return tomllib.loads(text)


def _apply(values: dict, raw: dict, profile: str | None) -> None:
    values.update(raw.get("defaults", {}))
    if profile is None:
        return
    section = raw.get("profile", {}).get(profile)
    if section is not None:
        values.update(section)


def load_config(repo: Path, profile: str | None = None, **overrides) -> Config:
    """Precedence: overrides > <repo>/.rh/rh.toml > packaged defaults.
    A profile section overrides [defaults] within the same file."""
    sources = [_read_default()]
    local = Path(repo) / ".rh" / "rh.toml"
    if local.is_file():
        sources.append(tomllib.loads(local.read_text()))

    if profile is not None and not any(profile in s.get("profile", {}) for s in sources):
        known = sorted({p for s in sources for p in s.get("profile", {})})
        raise ConfigError(f"unknown profile {profile!r}; known profiles: {', '.join(known)}")

    values: dict = {}
    for raw in sources:
        _apply(values, raw, profile)
    if os.environ.get("RH_JUDGE", "").lower() in ("0", "off", "false", "no"):
        values["judge"] = False
    values.update({k: v for k, v in overrides.items() if v is not None})

    names = {f.name for f in fields(Config)}
    cfg = Config(**{k: v for k, v in values.items() if k in names})
    cfg.profile = profile or "default"
    return cfg
