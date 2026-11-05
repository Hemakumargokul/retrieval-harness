from rh.analyze import analyze
from rh.config import Config
from rh.expand import expand, pick_symbols, should_expand
from rh.models import Diagnostic, Edge, Evidence


def ev(path, symbol=None, role="primary", start=1):
    return Evidence(path, start, start + 3, "colgrep", symbol=symbol, role=role)


def test_should_expand_triggers():
    a = analyze("who calls fuse everywhere in the code")
    assert should_expand(a, [ev("a.py")])
    plain = analyze("explain how the fuse function works please")
    assert not should_expand(plain, [ev("a.py"), ev("a.py", start=9)])
    assert should_expand(plain, [ev("a.py"), ev("b.py"), ev("c.py")])
    assert not should_expand(a, [])


def test_pick_symbols_prefers_named_over_popular(repo):
    fused = [ev("x.py", "Evidence"), ev("y.py", "fuse"), ev("z.py", "analyze")]
    a = analyze("find all callers of fuse and rename how it merges")
    assert pick_symbols(fused, a, repo) == ["fuse"]


def test_pick_symbols_falls_back_to_top_hits(repo):
    fused = [ev("x.py", "Evidence"), ev("y.py", "analyze")]
    assert pick_symbols(fused, analyze("something unrelated to anything named here"), repo) == ["Evidence", "analyze"]


def test_pick_symbols_uses_enclosing_python_function(repo):
    fused = [Evidence("pkg/billing.py", 12, 12, "ripgrep")]  # inside compute_total
    assert pick_symbols(fused, analyze("rename compute_total across the repo"), repo) == ["compute_total"]


class FakeCbm:
    def neighbors(self, project, symbols, direction, timeout_s):
        e = Evidence("c.py", 5, 9, "cbm", symbol="caller_fn", role=direction)
        dup = Evidence("a.py", 1, 4, "cbm", symbol="seen", role=direction)  # already in fused
        return [e, dup], [Edge("caller_fn", "fuse", "calls")], [Diagnostic("cbm", "info", "x")]


def test_expand_dedupes_and_scores(repo):
    fused = [ev("a.py", "fuse")]
    new, edges, diags = expand(fused, analyze("callers of fuse"), FakeCbm(), "proj", repo, Config(), 5)
    assert [(e.path, e.start) for e in new] == [("c.py", 5)]
    assert all(e.path != "a.py" for e in new)
    assert len(edges) == 1          # identical edges from both directions collapse
    assert all(e.score > 0 and e.rank >= 1 for e in new)
