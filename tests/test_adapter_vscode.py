import json

from rh.adapters.vscode import CONTEXT_FILE, handle


def test_writes_context_file_and_never_blocks(repo):
    out = handle({"prompt": "why does InvoiceTotaler.compute_total return the wrong total", "cwd": str(repo)})
    assert out["continue"] is True and "systemMessage" in out
    md = (repo / ".rh" / CONTEXT_FILE).read_text()
    assert "pkg/billing.py" in md
    assert json.loads((repo / ".rh" / "context.json").read_text())["items"]


def test_no_hits_overwrites_stale_context(repo):
    (repo / ".rh").mkdir()
    (repo / ".rh" / CONTEXT_FILE).write_text("OLD CONTEXT")
    out = handle({"prompt": "explain zzzNoSuchSymbolAnywhere please now", "cwd": str(repo)})
    assert out == {"continue": True}
    assert "OLD CONTEXT" not in (repo / ".rh" / CONTEXT_FILE).read_text()


def test_bad_payload_still_continues():
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "rh.cli", "hook", "vscode"], input="garbage",
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and json.loads(r.stdout) == {"continue": True}
