import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rh.config import Config, load_config
from rh.judge import build_messages, judge, parse_keep
from rh.models import ContextItem
from rh.retrieve import retrieve

TASK = "fix the bug in InvoiceTotaler.compute_total when items is empty"


def items(n):
    return [ContextItem(f"f{i}.py", 1, 20, "primary", "\n".join(f"line{j}" for j in range(20)), 50,
                        ["colgrep"], score=1.0 - i / 100) for i in range(n)]


class FakeOllama:
    """Minimal /api/chat server. `reply` is the message content (or a callable / (status, body))."""

    def __init__(self, reply):
        self.reply, self.requests = reply, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                r = outer.reply(body) if callable(outer.reply) else outer.reply
                status, payload = (r if isinstance(r, tuple) else (200, {"message": {"role": "assistant", "content": r}}))
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    made = []

    def make(reply):
        f = FakeOllama(reply)
        made.append(f)
        return f

    yield make
    for f in made:
        f.close()


def cfg_for(server, **kw):
    return Config(judge=True, judge_model="test-model", judge_endpoint=server.url, judge_timeout_s=5, **kw)


# ---- parsing
@pytest.mark.parametrize("content,expected", [
    ('{"keep": [2, 0]}', [2, 0]),
    ('{"keep": ["1", 1, 1]}', [1]),                       # numeric strings accepted, de-duplicated
    ('{"keep": [0, 99, -1, "x", null]}', [0]),            # out of range / junk ignored
    ('Sure! {"keep": [1]} hope that helps', [1]),         # JSON embedded in prose
    ('<think>hmm {"keep": [0]}</think>{"keep": [2]}', [2]),
    ('{"keep": []}', []), ('not json', []), ('{"other": 1}', []), ('[1,2]', []),
])
def test_parse_keep(content, expected):
    assert parse_keep(content, 3) == expected


def test_messages_truncate_snippets_and_number_excerpts():
    msgs = build_messages(TASK, items(2), snippet_lines=5)
    user = msgs[1]["content"]
    assert "[0] primary f0.py:1-20" in user and "[1] primary f1.py:1-20" in user
    assert "line4" in user and "line5" not in user and "(15 more lines)" in user
    assert TASK in user and msgs[0]["role"] == "system"


# ---- judge()
def test_disabled_is_a_noop():
    its = items(6)
    assert judge(TASK, its, Config(judge=False)) == (its, [], 0)


def test_filters_to_selected_excerpts_in_original_order(ollama):
    srv = ollama('{"keep": [3, 0]}')
    kept, diags, ms = judge(TASK, items(6), cfg_for(srv))
    assert [i.path for i in kept] == ["f0.py", "f3.py"]
    assert diags[0].level == "info" and "kept 2 of 6" in diags[0].message and ms >= 0


def test_request_shape(ollama):
    srv = ollama('{"keep": [0]}')
    judge(TASK, items(5), cfg_for(srv, judge_num_ctx=4096))
    path, body = srv.requests[0]
    assert path == "/api/chat" and body["model"] == "test-model" and body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_ctx": 4096}
    assert body["format"]["required"] == ["keep"]
    assert TASK in body["messages"][1]["content"]


def test_skipped_when_not_more_than_min_items(ollama):
    srv = ollama('{"keep": [0]}')
    its = items(3)
    kept, diags, _ = judge(TASK, its, cfg_for(srv, judge_min_items=3))
    assert kept == its and srv.requests == [] and "skipped" in diags[0].message


def test_no_model_configured_keeps_all_with_warning(monkeypatch):
    monkeypatch.delenv("RH_JUDGE_MODEL", raising=False)
    its = items(5)
    kept, diags, _ = judge(TASK, its, Config(judge=True))
    assert kept == its and diags[0].level == "warn" and "no model" in diags[0].message


def test_model_from_environment(ollama, monkeypatch):
    srv = ollama('{"keep": [1]}')
    monkeypatch.setenv("RH_JUDGE_MODEL", "env-model")
    cfg = Config(judge=True, judge_endpoint=srv.url)
    kept, _, _ = judge(TASK, items(5), cfg)
    assert [i.path for i in kept] == ["f1.py"] and srv.requests[0][1]["model"] == "env-model"


@pytest.mark.parametrize("reply", [
    "not json at all",
    '{"keep": []}',                       # empty answer is treated as unusable, not "drop everything"
    '{"keep": [42]}',                     # only out-of-range ids
    (500, {"error": "model not found"}),
    (200, b"<html>"),
])
def test_fails_open_on_bad_answers(ollama, reply):
    srv = ollama(reply)
    its = items(5)
    kept, diags, _ = judge(TASK, its, cfg_for(srv))
    assert kept == its and diags[0].level == "warn" and "kept all" in diags[0].message


def test_fails_open_when_ollama_is_down():
    its = items(5)
    cfg = Config(judge=True, judge_model="m", judge_endpoint="http://127.0.0.1:9", judge_timeout_s=2)
    kept, diags, _ = judge(TASK, its, cfg)
    assert kept == its and "unreachable" in diags[0].message


def test_fails_open_on_timeout(ollama):
    import time
    srv = ollama(lambda body: (time.sleep(1.5), '{"keep": [0]}')[1])
    its = items(5)
    cfg = cfg_for(srv)
    cfg.judge_timeout_s = 0.3
    t0 = time.monotonic()
    kept, diags, _ = judge(TASK, its, cfg)
    assert kept == its and "kept all" in diags[0].message and time.monotonic() - t0 < 1.2


# ---- config + pipeline
def test_config_defaults_and_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("RH_JUDGE")
    c0 = load_config(tmp_path)
    assert (c0.judge, c0.judge_model, c0.judge_autostart) == (True, "qwen3.5:4b", True)
    monkeypatch.setenv("RH_JUDGE", "0")
    assert load_config(tmp_path).judge is False
    monkeypatch.delenv("RH_JUDGE")
    (tmp_path / ".rh").mkdir()
    (tmp_path / ".rh" / "rh.toml").write_text('[defaults]\njudge = true\njudge_model = "qwen"\njudge_min_items = 1\n')
    c = load_config(tmp_path)
    assert (c.judge, c.judge_model, c.judge_min_items) == (True, "qwen", 1)
    assert load_config(tmp_path, judge=False).judge is False   # CLI --no-judge wins


def test_pipeline_applies_judge_before_budget(repo, ollama):
    from rh.engines.base import EngineResult
    from rh.models import Evidence
    from tests.test_core import FakeEngine

    evs = [Evidence("pkg/billing.py", 12, 12, "ripgrep", rank=0),
           Evidence("tests/test_billing.py", 5, 5, "ripgrep", rank=1, role="test"),
           Evidence("README.md", 3, 3, "ripgrep", rank=2, role="doc"),
           Evidence("pkg/billing.py", 2, 2, "ripgrep", rank=3)]
    srv = ollama(lambda body: '{"keep": [0]}')
    cfg = cfg_for(srv, judge_min_items=1)
    pkg = retrieve(repo, TASK, cfg, [FakeEngine("ripgrep", evs)])
    assert len(srv.requests) == 1
    # compute_total is a ripgrep-confirmed definition of the anchor: pinned, plus the one the model chose
    assert len(pkg.items) >= 2 and any("compute_total" in i.text for i in pkg.items)
    assert "judge" in pkg.timings_ms
    assert any(d.engine == "judge" and " pinned" in d.message for d in pkg.diagnostics)


def test_pipeline_unchanged_when_judge_off(repo):
    from rh.models import Evidence
    from tests.test_core import FakeEngine

    evs = [Evidence("pkg/billing.py", 12, 12, "ripgrep"), Evidence("README.md", 3, 3, "ripgrep", rank=1, role="doc")]
    pkg = retrieve(repo, TASK, Config(), [FakeEngine("ripgrep", evs)])
    assert "judge" not in pkg.timings_ms and len(pkg.items) == 2


def test_request_disables_thinking_and_unloads_model(ollama):
    srv = ollama('{"keep": [0]}')
    judge(TASK, items(5), cfg_for(srv))
    _, body = srv.requests[0]
    assert body["think"] is False and body["keep_alive"] == 0


def test_autostarts_ollama_and_stops_it_afterwards(tmp_path, monkeypatch):
    import socket
    import stat
    import sys

    from rh import judge as J

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    fake = tmp_path / "ollama"
    pidfile = tmp_path / "pid"
    fake.write_text(f"""#!{sys.executable}
import os, http.server
open({str(pidfile)!r}, "w").write(str(os.getpid()))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", {port}), H).serve_forever()
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    endpoint = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(J, "DEFAULT_ENDPOINT", endpoint)
    monkeypatch.setattr(J.shutil, "which", lambda n: str(fake))
    rec: dict = {}
    with J.ollama_session(Config(judge_endpoint=endpoint), rec):
        assert J._alive(endpoint)
    assert "started" in rec["ollama"]
    assert not J._alive(endpoint)
    # autostart can be switched off
    rec2: dict = {}
    with J.ollama_session(Config(judge_endpoint=endpoint, judge_autostart=False), rec2):
        assert not J._alive(endpoint)
    assert "ollama" not in rec2


def test_pin_rule():
    from rh.judge import is_pinned
    from rh.models import ContextItem

    def it(text, sources=("ripgrep",)):
        return ContextItem("a.py", 1, 5, "primary", text, 10, list(sources))

    anchors = ["rank_by_anchor_coverage"]
    assert is_pinned(it("def rank_by_anchor_coverage(hits):\n    ..."), anchors)           # the definition
    assert is_pinned(it("def test_rank_by_anchor_coverage():\n    ..."), anchors)         # the test named after it
    assert is_pinned(it("export const rank_by_anchor_coverage = () => 1"), anchors)
    assert not is_pinned(it("from x import rank_by_anchor_coverage\n"), anchors)          # import only
    assert not is_pinned(it('{"name": "rank_by_anchor_coverage"}'), anchors)               # data / fixture
    assert not is_pinned(it("def rank_by_anchor_coverage(h): ...", ("colgrep",)), anchors)  # not found by ripgrep
    assert not is_pinned(it("def other(): ..."), ["a", "xy"])                              # no usable anchor


def test_pinned_items_are_never_dropped_or_shown_to_the_model(ollama):
    from rh.models import ContextItem

    its = items(6)
    its[2] = ContextItem("pin.py", 1, 5, "primary", "def compute_total(x):\n    return 1", 10, ["ripgrep"])
    srv = ollama('{"keep": []}')           # the model keeps nothing...
    kept, diags, _ = judge(TASK, its, cfg_for(srv), anchors=["compute_total"])
    assert kept == its                      # ...unusable answer -> everything kept
    srv2 = ollama('{"keep": [0]}')
    rec: dict = {}
    kept, _, _ = judge(TASK, its, cfg_for(srv2), record=rec, anchors=["compute_total"])
    assert [i.path for i in kept] == ["f0.py", "pin.py"] and rec["pinned"] == ["pin.py:1-5"]
    assert "pin.py" not in srv2.requests[0][1]["messages"][1]["content"]
