from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run(cmd: list[str], cwd: Path | None, timeout_s: float) -> tuple[int, str, str, int]:
    """Run a command; never raises. rc=-1 on timeout, rc=127 if the binary is missing."""
    t0 = time.monotonic()
    try:
        p = subprocess.run(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s,
            errors="replace",
        )
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", f"timed out after {timeout_s}s"
    except FileNotFoundError:
        rc, out, err = 127, "", f"{cmd[0]}: command not found"
    except OSError as e:
        rc, out, err = 126, "", str(e)
    return rc, out, err, int((time.monotonic() - t0) * 1000)


def find_binary(name: str) -> str | None:
    """$RH_<NAME>_BIN, PATH, this venv's bin/, then <project>/.tools/bin/."""
    env = os.environ.get("RH_" + name.upper().replace("-", "_") + "_BIN")
    if env and os.access(env, os.X_OK):
        return env
    found = shutil.which(name)
    if found:
        return found
    for d in (Path(sys.prefix) / "bin", Path(__file__).resolve().parents[3] / ".tools" / "bin"):
        cand = d / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None
