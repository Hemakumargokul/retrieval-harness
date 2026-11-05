import time

import pytest

from rh.analyze import analyze, should_skip
from rh.config import ConfigError, Config, load_config
from rh.engines.base import EngineResult, EngineStatus, role_for_path
from rh.engines.ripgrep import parse_rg_json, rank_by_anchor_coverage
from rh.fuse import fuse
from rh.models import ContextPackage, ContextItem, Diagnostic, Edge, Evidence
from rh.pack import excerpt, pack, verify
from rh.render import to_markdown
from rh.retrieve import retrieve
from rh.state import State, is_graph_stale, load_state, save_state


# ---- models
def test_package_roundtrip():
    pkg = ContextPackage(task="t", profile="p",
                         items=[ContextItem("a.py", 1, 2, "primary", "x", 1, ["ripgrep"])],
                         references=[Evidence("b.py", 3, 4, "ripgrep")],
                         edges=[Edge("a", "b", "calls")],
                         diagnostics=[Diagnostic("rg", "warn", "m")], timings_ms={"total": 5})
    assert ContextPackage.from_dict(pkg.to_dict()) == pkg


# ---- config
def test_profile_overrides_defaults(tmp_path):
    cfg = load_config(tmp_path, "local-small")
    assert (cfg.budget_tokens, cfg.deadline_s) == (2500, 6)
    assert load_config(tmp_path).budget_tokens == 4000


def test_unknown_profile(tmp_path):
    with pytest.raises(ConfigError, match="nope"):
        load_config(tmp_path, "nope")


def test_repo_config_and_override_precedence(tmp_path):
    (tmp_path / ".rh").mkdir()
    (tmp_path / ".rh" / "rh.toml").write_text("[defaults]\nbudget_tokens = 111\ntop_k = 3\n")
    assert load_config(tmp_path).budget_tokens == 111
    assert load_config(tmp_path, budget_tokens=999).budget_tokens == 999
    assert load_config(tmp_path).top_k == 3


# ---- state
def test_state_missing_and_roundtrip(tmp_path):
    assert load_state(tmp_path) == State()
    save_state(tmp_path, State(cbm_project="p", cbm_head="abc"))
    assert load_state(tmp_path).cbm_project == "p"


def test_staleness_non_git_is_not_stale(tmp_path):
    assert is_graph_stale(State(cbm_head="abc"), tmp_path) is False


# ---- analyze
@pytest.mark.parametrize("prompt", ["thanks", "ok", "commit this", "/help", "", "fix it", "push"])
def test_skip(prompt):
    assert should_skip(prompt)


@pytest.mark.parametrize("prompt", [
    "fix the bug in InvoiceTotaler.compute_total when items is empty",
    "why does pkg/billing.py raise KeyError on discount",
    "rename compute_total across the codebase",
])
def test_no_skip(prompt):
    assert not should_skip(prompt)


def test_analyze_extracts_anchors():
    a = analyze("Fix `compute_total` in pkg/billing.py: InvoiceTotaler raises\nValueError: bad price 'abc'")
    assert "pkg/billing.py" in a.paths
    assert "compute_total" in a.quoted and "InvoiceTotaler" in a.identifiers
    assert any("ValueError" in e for e in a.errors)
    assert a.anchors()[0].startswith("ValueError")
    assert not a.cross_file


def test_analyze_cross_file():
    assert analyze("find all callers of parse_config and rename it").cross_file
    assert "fuse.py" in analyze("look at fuse.py please now").paths


def test_analyze_ignores_plain_words():
    assert analyze("please make the code better and faster now").identifiers == []


# ---- ripgrep parsing
RG = '\n'.join([
    '{"type":"begin","data":{"path":{"text":"./pkg/billing.py"}}}',
    '{"type":"match","data":{"path":{"text":"./pkg/billing.py"},"line_number":9}}',
    '{"type":"match","data":{"path":{"text":"tests/test_billing.py"},"line_number":4}}',
    'not json',
    '{"type":"summary","data":{}}',
])


def test_parse_rg_json():
    evs = parse_rg_json(RG, "q")
    assert [(e.path, e.start, e.role) for e in evs] == [
        ("pkg/billing.py", 9, "primary"), ("tests/test_billing.py", 4, "test")]


def test_roles():
    assert role_for_path("README.md") == "doc"
    assert role_for_path("a/__tests__/x.js") == "test"
    assert role_for_path("src/foo.spec.ts") == "test"
    assert role_for_path("src/foo.py") == "primary"


def test_rank_by_anchor_coverage():
    a = [Evidence("x.py", 1, 1, "ripgrep", query="q1"), Evidence("y.py", 1, 1, "ripgrep", query="q1"),
         Evidence("y.py", 5, 5, "ripgrep", query="q2")]
    assert rank_by_anchor_coverage(a)[0].path == "y.py"


# ---- fuse
def test_fuse_merges_and_prefers_multi_engine():
    r1 = EngineResult([Evidence("a.py", 10, 10, "ripgrep", rank=0), Evidence("b.py", 1, 1, "ripgrep", rank=1)])
    r2 = EngineResult([Evidence("a.py", 12, 14, "colgrep", rank=3)])
    out = fuse([r1, r2])
    assert out[0].path == "a.py" and (out[0].start, out[0].end) == (10, 14)
    assert set(out[0].sources) == {"ripgrep", "colgrep"}
    assert len(out) == 2


def test_fuse_keeps_distant_ranges_apart():
    r = EngineResult([Evidence("a.py", 1, 1, "ripgrep"), Evidence("a.py", 50, 50, "ripgrep", rank=1)])
    assert len(fuse([r])) == 2


# ---- pack
def test_verify_drops_missing_and_out_of_range(repo):
    assert verify(Evidence("nope.py", 1, 1, "ripgrep"), repo) is None
    assert verify(Evidence("pkg/billing.py", 999, 999, "ripgrep"), repo) is None


def test_excerpt_widens_to_enclosing_method(repo):
    s, e, text = excerpt(Evidence("pkg/billing.py", 12, 12, "ripgrep"), repo)
    assert text.startswith("    def compute_total") and text.rstrip().endswith("return total")
    assert (s, e) == (9, 13)


def test_excerpt_respects_max_lines(repo):
    big = repo / "big.txt"
    big.write_text("\n".join(f"line{i}" for i in range(500)))
    s, e, text = excerpt(Evidence("big.txt", 250, 250, "ripgrep"), repo, max_lines=20)
    assert e - s + 1 <= 20 and "line249" in text


def _item(role, tokens, score=0.0, path="a"):
    return ContextItem(path, 1, 2, role, "x", tokens, score=score)


def test_pack_budget_and_priority():
    items = [_item("doc", 10), _item("primary", 50, path="p"), _item("test", 30, path="t"), _item("primary", 500, path="big")]
    kept, over = pack(items, budget=100)  # 90 usable after reserve
    assert sum(i.tokens for i in kept) <= 90
    assert [i.path for i in kept][:2] == ["p", "t"]
    assert len(kept) + len(over) == len(items)
    assert any(i.path == "big" for i in over)


# ---- render
def test_render_skipped_is_empty():
    assert to_markdown(ContextPackage("t", "p", skipped=True)) == ""


def test_render_mermaid_only_with_enough_edges():
    base = ContextPackage("t", "p", items=[ContextItem("a.py", 1, 2, "primary", "x = 1", 2, ["ripgrep"])])
    assert "mermaid" not in to_markdown(base, 3)
    base.edges = [Edge("a", "b", "calls")] * 3
    assert "```mermaid" in to_markdown(base, 3)


# ---- orchestrator
class FakeEngine:
    def __init__(self, name, evidence=(), status=EngineStatus.OK, delay=0.0, boom=False):
        self.name, self._ev, self._st, self._delay, self._boom = name, list(evidence), status, delay, boom

    def status(self, repo, state):
        return self._st

    def search(self, repo, queries, timeout_s):
        time.sleep(self._delay)
        if self._boom:
            raise RuntimeError("kaboom")
        return EngineResult(self._ev)


TASK = "fix the bug in InvoiceTotaler.compute_total when items is empty"


def test_retrieve_with_fake_engine(repo):
    eng = FakeEngine("ripgrep", [Evidence("pkg/billing.py", 12, 12, "ripgrep"),
                                 Evidence("tests/test_billing.py", 5, 5, "ripgrep", role="test")])
    pkg = retrieve(repo, TASK, Config(), [eng])
    assert {i.path for i in pkg.items} == {"pkg/billing.py", "tests/test_billing.py"}
    assert pkg.token_estimate > 0 and "total" in pkg.timings_ms


def test_retrieve_skips_trivial(repo):
    assert retrieve(repo, "thanks", Config(), [FakeEngine("ripgrep")]).skipped


def test_retrieve_all_engines_missing(repo):
    pkg = retrieve(repo, TASK, Config(), [FakeEngine("ripgrep", status=EngineStatus.MISSING)])
    assert pkg.items == [] and pkg.diagnostics[0].message == "skipped: missing"


def test_retrieve_engine_exception_is_contained(repo):
    pkg = retrieve(repo, TASK, Config(), [FakeEngine("ripgrep", boom=True)])
    assert any("kaboom" in d.message for d in pkg.diagnostics)


def test_retrieve_hung_engine_respects_deadline(repo):
    cfg = Config(deadline_s=0.5, per_engine_timeout_s=0.3)
    t0 = time.monotonic()
    pkg = retrieve(repo, TASK, cfg, [FakeEngine("ripgrep", delay=3)])
    assert time.monotonic() - t0 < 1.5
    assert any("deadline" in d.message for d in pkg.diagnostics)


@pytest.mark.integration
def test_real_ripgrep_end_to_end(repo):
    pkg = retrieve(repo, "why does InvoiceTotaler.compute_total return wrong total", Config())
    paths = {i.path for i in pkg.items}
    assert "pkg/billing.py" in paths
