from pathlib import Path

from rh.engines.cbm import parse_locate, parse_neighbors
from rh.engines.colgrep import parse_colgrep_json

FIX = Path(__file__).parent / "fixtures"
REPO = Path("/home/hem/projects/retrieval-harness")  # absolute paths in the recorded fixture


def test_colgrep_uses_matches_not_whole_file_unit():
    evs = parse_colgrep_json((FIX / "colgrep_search.json").read_text(), REPO, "q")
    first = evs[0]
    assert (first.path, first.symbol, first.start, first.end) == ("src/rh/fuse.py", "fuse", 9, 44)
    assert all(not e.path.startswith("/") for e in evs)
    assert [e.rank for e in evs] == list(range(len(evs)))
    assert not any(e.symbol == "" for e in evs)
    # per-file cap and the 0.5x-of-best score cutoff
    assert sum(e.path == "src/rh/engines/base.py" for e in evs) <= 2
    syms = {e.symbol for e in evs}
    assert "MERGE_GAP" in syms           # 0.74 >= 0.5 * 1.11, kept
    assert "EngineStatus" not in syms    # 0.36 < 0.5 * 0.80, dropped


def test_colgrep_bad_json_is_empty():
    assert parse_colgrep_json("not json", REPO) == []
    assert parse_colgrep_json("{}", REPO) == []


def test_cbm_locate_fixture():
    evs = parse_locate((FIX / "cbm_query_locate.json").read_text())
    assert [(e.path, e.start, e.end, e.symbol) for e in evs] == [("src/rh/fuse.py", 9, 44, "fuse")]


def test_cbm_callers_fixture_tags_tests_and_edges():
    evs, edges = parse_neighbors((FIX / "cbm_query_callers.json").read_text(), "caller")
    roles = {e.symbol: e.role for e in evs}
    assert roles["_retrieve"] == "caller"
    assert roles["test_fuse_merges_and_prefers_multi_engine"] == "test"
    assert any(e.src == "_retrieve" and e.dst == "fuse" for e in edges)


def test_cbm_callees_fixture_edge_direction():
    evs, edges = parse_neighbors((FIX / "cbm_query_callees.json").read_text(), "callee")
    assert evs[0].role == "callee"
    assert edges[0].src == "retrieve" and edges[0].dst == "_retrieve"


def test_cbm_garbage_is_empty():
    assert parse_locate("nope") == []
    assert parse_neighbors("", "caller") == ([], [])


def test_cbm_builtin_pseudo_files_are_dropped():
    raw = ('{"rows":[["fuse","append","<python-builtins>","1","1"],'
           '["fuse","helper","src/x.py","3","9"],["fuse","bad","src/y.py","","9"]]}')
    evs, edges = parse_neighbors(raw, "callee")
    assert [e.symbol for e in evs] == ["helper"] and len(edges) == 1


def test_colgrep_search_always_returns_a_result(tmp_path, monkeypatch):
    from rh.engines import colgrep as C

    cmds = []

    def fake_run(cmd, cwd, timeout):
        cmds.append(cmd)
        return 0, (Path(__file__).parent / "fixtures" / "colgrep_search.json").read_text(), "", 5

    monkeypatch.setattr(C, "find_binary", lambda n: "/bin/colgrep")
    monkeypatch.setattr(C, "run", fake_run)
    res = C.ColgrepEngine().search(tmp_path, ["q"], 5)
    assert res is not None and res.evidence and "--no-update" in cmds[0] and "--agent" not in cmds[0]
    monkeypatch.setattr(C, "run", lambda *a: (1, "", "boom", 5))
    assert C.ColgrepEngine().search(tmp_path, ["q"], 5).diagnostics
