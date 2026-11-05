from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rh.config import ConfigError, load_config
from rh.render import to_json, to_markdown
from rh.retrieve import retrieve
from rh.trace import Trace, trace_enabled, write_run


def _cmd_retrieve(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        cfg = load_config(repo, args.profile, budget_tokens=args.budget, judge=args.judge,
                          judge_model=args.judge_model)
    except ConfigError as e:
        print(f"rh: {e}", file=sys.stderr)
        return 2
    trace = Trace() if args.trace is not False and trace_enabled() else None
    pkg = retrieve(repo, args.task, cfg, trace=trace)
    if trace is not None:
        try:
            root = Path(args.trace) if args.trace else None
            print(f"trace: {write_run(trace, pkg, cfg, repo, root)}", file=sys.stderr)
        except OSError as e:
            print(f"rh: could not write trace: {e}", file=sys.stderr)
    md = to_markdown(pkg, cfg.mermaid_min_edges)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "context.md").write_text(md)
        (out / "context.json").write_text(to_json(pkg))
    elif args.format == "json":
        print(to_json(pkg))
    else:
        sys.stdout.write(md)
    return 0


def _cmd_hook(args: argparse.Namespace) -> int:
    if args.agent == "vscode":
        from rh.adapters import vscode

        return vscode.run(args.profile or "local-small")
    from rh.adapters import claude_code

    return claude_code.run(args.profile or "claude-code")


def _cmd_init(args: argparse.Namespace) -> int:
    from rh import lifecycle

    repo = Path(args.repo).resolve()
    if args.if_stale:
        if args.background:
            lifecycle.reindex_in_background(repo)
            return 0
        from rh.state import is_graph_stale, load_state

        st = load_state(repo)
        if st.cbm_project and not is_graph_stale(st, repo):
            return 0
    lifecycle.init(repo)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    import json as _json
    from dataclasses import asdict

    from rh import lifecycle

    rows = lifecycle.doctor(Path(args.repo).resolve())
    if args.json:
        print(_json.dumps([asdict(r) for r in rows], indent=2))
    else:
        for r in rows:
            print(f"{r.engine:<10} {r.status:<10} {r.detail}")
    return 0 if any(r.status in ("ok", "stale") and r.engine != "cbm" for r in rows) else 1


def _cmd_mcp(args: argparse.Namespace) -> int:
    from rh.adapters import mcp_server

    return mcp_server.run(args.profile)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rh", description="Retrieval harness for coding agents")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("retrieve", help="build a context package for a task")
    r.add_argument("--repo", default=".")
    r.add_argument("--task", required=True)
    r.add_argument("--profile")
    r.add_argument("--budget", type=int)
    r.add_argument("--judge", action=argparse.BooleanOptionalAction, default=None,
                   help="filter excerpts with a local Ollama model (default: from config)")
    r.add_argument("--judge-model", help="Ollama model for the judge, e.g. qwen2.5-coder:7b")
    r.add_argument("--trace", nargs="?", const=None, default=None, metavar="DIR",
                   help="trace directory (default: <repo>/.rh/trace); tracing is on unless --no-trace")
    r.add_argument("--no-trace", dest="trace", action="store_const", const=False,
                   help="do not write trace.html / trace.json")
    r.add_argument("--out", help="directory to write context.md and context.json")
    r.add_argument("--format", choices=["md", "json"], default="md")
    r.set_defaults(func=_cmd_retrieve)

    h = sub.add_parser("hook", help="run as an agent hook (reads the hook payload on stdin)")
    h.add_argument("agent", choices=["claude-code", "vscode"])
    h.add_argument("--profile")
    h.set_defaults(func=_cmd_hook)

    i = sub.add_parser("init", help="build the ColGREP and graph indexes")
    i.add_argument("--repo", default=".")
    i.add_argument("--if-stale", action="store_true", help="only re-index when the graph is stale")
    i.add_argument("--background", action="store_true", help="with --if-stale: detach and return")
    i.set_defaults(func=_cmd_init)

    d = sub.add_parser("doctor", help="report the state of each engine")
    d.add_argument("--repo", default=".")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_doctor)

    m = sub.add_parser("mcp", help="run the MCP stdio server (needs the 'mcp' extra)")
    m.add_argument("--profile")
    m.set_defaults(func=_cmd_mcp)

    args, _ = p.parse_known_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
