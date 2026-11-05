from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class State:
    cbm_project: str | None = None
    colgrep_model: str | None = None
    colgrep_built_at: float | None = None
    cbm_built_at: float | None = None
    cbm_head: str | None = None


def state_path(repo: Path) -> Path:
    return Path(repo) / ".rh" / "state.json"


def load_state(repo: Path) -> State:
    try:
        data = json.loads(state_path(repo).read_text())
        return State(**{k: v for k, v in data.items() if k in State.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return State()


def save_state(repo: Path, state: State) -> None:
    p = state_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2))


def git_head(repo: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def is_graph_stale(state: State, repo: Path) -> bool:
    """Stale when the graph was built at a different HEAD. Unknown HEAD is not stale."""
    head = git_head(repo)
    if head is None or state.cbm_head is None:
        return False
    return head != state.cbm_head
