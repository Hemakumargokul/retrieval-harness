import json

from rh.adapters.claude_code import handle


def test_injects_additional_context(repo):
    out = handle({"prompt": "why does InvoiceTotaler.compute_total return the wrong total", "cwd": str(repo)})
    data = json.loads(out)["hookSpecificOutput"]
    assert data["hookEventName"] == "UserPromptSubmit"
    assert "pkg/billing.py" in data["additionalContext"]


def test_trivial_prompt_injects_nothing(repo):
    assert handle({"prompt": "thanks", "cwd": str(repo)}) == ""


def test_no_hits_injects_nothing(repo):
    assert handle({"prompt": "explain zzzNoSuchSymbolAnywhere please", "cwd": str(repo)}) == ""


def test_works_when_stdin_is_a_pipe(repo):
    """Regression: rg searches stdin instead of the repo when stdin is piped, as in a real hook."""
    import subprocess, sys
    payload = json.dumps({"prompt": "why does InvoiceTotaler.compute_total return the wrong total", "cwd": str(repo)})
    r = subprocess.run([sys.executable, "-m", "rh.cli", "hook", "claude-code"], input=payload,
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and "pkg/billing.py" in r.stdout


def test_output_stays_under_claude_code_cap(tmp_path):
    from rh.adapters.claude_code import MAX_CHARS
    for n in range(40):
        (tmp_path / f"mod{n}.py").write_text("\n".join(f"def widget_{n}_{i}():\n    return {i}" for i in range(30)))
    out = handle({"prompt": "explain every widget function in the codebase please", "cwd": str(tmp_path)})
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    assert len(ctx) <= MAX_CHARS
    assert ctx.count("```") % 2 == 0  # no unterminated code fence


def test_fit_chars_cuts_on_section_boundary():
    from rh.render import fit_chars
    md = "# t\n\n### a\n\n```\nxxxx\n```\n\n### b\n\n```\n" + "y" * 500 + "\n```\n"
    out = fit_chars(md, 80)
    assert out.endswith("```\n") and "### b" not in out
