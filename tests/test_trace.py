import json
import os
from html.parser import HTMLParser

import pytest

from rh.config import Config
from rh.models import Evidence
from rh.retrieve import retrieve
from rh.trace import Trace, env_trace
from tests.test_core import FakeEngine
from tests.test_judge import FakeOllama

TASK = "fix the bug in InvoiceTotaler.compute_total when items is empty"
EVS = [Evidence("pkg/billing.py", 12, 12, "ripgrep", rank=0, query="InvoiceTotaler"),
       Evidence("tests/test_billing.py", 5, 5, "ripgrep", rank=1, role="test"),
       Evidence("README.md", 3, 3, "colgrep", rank=2, role="doc"),
       Evidence("gone.py", 1, 1, "colgrep", rank=3)]          # file does not exist: dropped by verify


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.text = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.text.append(data)


def traced(repo, cfg=None, engines=None):
    t = Trace()
    pkg = retrieve(repo, TASK, cfg or Config(), engines or [FakeEngine("ripgrep", EVS)], trace=t)
    return t, pkg


def test_every_stage_is_recorded(repo):
    t, pkg = traced(repo)
    assert {"gate", "analyze", "engines", "search", "fuse", "expand", "excerpt", "judge", "budget"} <= set(t.stages)
    s = t.stages
    assert "compute_total" in s["analyze"]["identifiers"] and "InvoiceTotaler" in s["analyze"]["identifiers"]
    assert s["search"]["engines"][0]["name"] == "ripgrep" and len(s["search"]["engines"][0]["hits"]) == 4
    assert s["fuse"]["total"] == 4
    assert s["expand"]["decision"].startswith("not run")
    assert s["judge"]["decision"] == "off"
    assert s["budget"]["used"] > 0 and s["budget"]["kept"]


def test_trace_is_json_serialisable_and_snapshotted(repo):
    t, pkg = traced(repo)
    data = t.to_dict(pkg, Config(), repo)
    json.dumps(data)                                         # must not raise
    assert data["package"]["task"] == TASK and "Retrieved context" in data["output_markdown"]


def test_excerpt_stage_shows_verify_drop_and_widening(repo):
    t, _ = traced(repo)
    exc = t.stages["excerpt"]
    assert any(c["path"] == "gone.py" for c in exc["candidates"])
    assert not any(i["path"] == "gone.py" for i in exc["items"])
    widened = next(i for i in exc["items"] if i["path"] == "pkg/billing.py")
    assert (widened["start"], widened["end"]) == (9, 13)    # whole method, from a 1-line hit


def test_tracing_does_not_change_the_result(repo):
    plain = retrieve(repo, TASK, Config(), [FakeEngine("ripgrep", EVS)])
    _, with_trace = traced(repo)
    assert [(i.path, i.start, i.end) for i in plain.items] == [(i.path, i.start, i.end) for i in with_trace.items]


def test_html_is_valid_self_contained_and_complete(repo, tmp_path):
    t, pkg = traced(repo)
    path = t.write(tmp_path / "out", pkg, Config(), repo)
    html = path.read_text()
    p = Collect()
    p.feed(html)
    assert "script" not in p.tags and "link" not in p.tags and "img" not in p.tags   # no JS / external files
    assert p.tags.count("details") == 8
    text = " ".join(p.text)
    for needle in ("Task analysis", "Search (raw, per engine)", "Fusion", "Graph expansion", "Excerpts",
                   "LLM judge", "Token budget", "Final output", "pkg/billing.py:12-12", "widened 1 → 5 lines",
                   "dropped (file missing"):
        assert needle in text, needle
    assert (tmp_path / "out" / "trace.json").exists()


def test_html_escapes_repo_content(repo, tmp_path):
    (repo / "pkg" / "evil.py").write_text('def f():\n    return "</pre><script>alert(1)</script>"\n')
    evs = [Evidence("pkg/evil.py", 2, 2, "ripgrep")]
    t = Trace()
    pkg = retrieve(repo, TASK, Config(), [FakeEngine("ripgrep", evs)], trace=t)
    html = t.write(tmp_path, pkg, Config(), repo).read_text()
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html


def test_judge_kept_and_dropped_are_visible(repo, tmp_path):
    srv = FakeOllama('{"keep": [0]}')
    try:
        cfg = Config(judge=True, judge_model="m", judge_endpoint=srv.url, judge_min_items=1, judge_timeout_s=5)
        t, pkg = traced(repo, cfg)
    finally:
        srv.close()
    j = t.stages["judge"]
    assert j["decision"] == "ran" and len(j["kept"]) >= 1 and len(j["dropped"]) >= 1 and j["pinned"]
    assert j["raw"] == '{"keep": [0]}' and "Task:" in j["prompt"]
    html = t.write(tmp_path, pkg, cfg, repo).read_text()
    assert "dropped</span>" in html and "kept</span>" in html and "prompt and raw answer" in html


def test_judge_failure_is_traced(repo):
    cfg = Config(judge=True, judge_model="m", judge_endpoint="http://127.0.0.1:9", judge_min_items=1, judge_timeout_s=2)
    t, _ = traced(repo, cfg)
    assert t.stages["judge"]["decision"] == "ran, failed: kept all" and "unreachable" in t.stages["judge"]["error"]


def test_skipped_prompt_renders(repo, tmp_path):
    t = Trace()
    pkg = retrieve(repo, "thanks", Config(), [FakeEngine("ripgrep")], trace=t)
    html = t.write(tmp_path, pkg, Config(), repo).read_text()
    assert "skipped by the gate" in html


def test_graph_expansion_traced_with_reason(repo):
    from rh.engines.cbm import CbmEngine
    from rh.engines.base import EngineStatus

    class FakeCbm(CbmEngine):
        def status(self, repo, state):
            return EngineStatus.OK

        def neighbors(self, project, symbols, direction, timeout_s):
            from rh.models import Edge
            if direction == "caller":
                return [Evidence("tests/test_billing.py", 4, 5, "cbm", symbol="test_total", role="test")], \
                       [Edge("test_total", "compute_total", "calls")], []
            return [], [], []

    t = Trace()
    retrieve(repo, "find all callers of compute_total and rename it", Config(),
             [FakeEngine("ripgrep", EVS[:1]), FakeCbm()], trace=t)
    e = t.stages["expand"]
    assert e["decision"] == "ran" and "cross-file" in e["reason"]
    assert "compute_total" in e["symbols"] and e["edges"] == [["test_total", "compute_total", "calls"]]


def test_hooks_trace_into_repo_by_default(repo, monkeypatch):
    from rh.adapters.claude_code import handle

    monkeypatch.delenv("RH_TRACE_DIR", raising=False)
    monkeypatch.delenv("RH_TRACE", raising=False)
    handle({"prompt": "why does InvoiceTotaler.compute_total return a wrong total", "cwd": str(repo)})
    root = repo / ".rh" / "trace"
    runs = [d for d in root.iterdir() if d.name != "latest"]
    assert len(runs) == 1 and (runs[0] / "trace.html").exists()
    assert (root / "latest" / "trace.json").exists()


def test_trace_opt_out_dir_override_and_prune(repo, tmp_path, monkeypatch):
    from rh.adapters.claude_code import handle
    from rh import trace as T

    monkeypatch.setenv("RH_TRACE", "0")
    assert env_trace() is None
    handle({"prompt": "why does InvoiceTotaler.compute_total return a wrong total", "cwd": str(repo)})
    assert not (repo / ".rh" / "trace").exists()
    monkeypatch.delenv("RH_TRACE")
    monkeypatch.setenv("RH_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setattr(T, "KEEP", 2)
    for _ in range(4):
        handle({"prompt": "why does InvoiceTotaler.compute_total return a wrong total", "cwd": str(repo)})
    runs = [d for d in (tmp_path / "traces").iterdir() if d.name != "latest"]
    assert len(runs) == 2
