from __future__ import annotations

import json
import re

from rh.models import ContextItem, ContextPackage

SECTIONS = [
    ("primary", "Relevant code"),
    ("test", "Tests"),
    ("caller", "Callers"),
    ("callee", "Callees"),
    ("doc", "Docs"),
]


def make_summary(pkg: ContextPackage) -> str:
    files = {i.path for i in pkg.items}
    tests = sum(1 for i in pkg.items if i.role == "test")
    primary = list(dict.fromkeys(i.path for i in pkg.items if i.role == "primary"))[:3]
    graph = "yes" if pkg.edges else "no"
    return (f"{len(pkg.items)} excerpts across {len(files)} files"
            f" (top: {', '.join(primary) or 'none'}); {tests} related tests;"
            f" graph expanded: {graph}; ~{pkg.token_estimate} tokens.")


def _mermaid(pkg: ContextPackage) -> str:
    def node(s: str) -> str:
        return re.sub(r"\W", "_", s)

    lines = ["```mermaid", "graph LR"]
    for e in pkg.edges:
        lines.append(f'  {node(e.src)}["{e.src}"] -->|{e.kind}| {node(e.dst)}["{e.dst}"]')
    lines.append("```")
    return "\n".join(lines)


def _fence(item: ContextItem) -> str:
    lang = item.path.rsplit(".", 1)[-1] if "." in item.path else ""
    fence = "````" if "```" in item.text else "```"
    return f"{fence}{lang}\n{item.text}\n{fence}"


def to_markdown(pkg: ContextPackage, mermaid_min_edges: int = 3) -> str:
    if pkg.skipped:
        return ""
    out = ["# Retrieved context", "", pkg.summary or make_summary(pkg), ""]
    for role, title in SECTIONS:
        group = [i for i in pkg.items if i.role == role]
        if not group:
            continue
        out += [f"## {title}", ""]
        for it in group:
            out += [f"### {it.path}:{it.start}-{it.end}  _({', '.join(it.sources)})_", "", _fence(it), ""]
    if len(pkg.edges) >= mermaid_min_edges:
        out += ["## Call graph", "", _mermaid(pkg), ""]
    if pkg.references:
        out += ["## Other references (not included)", ""]
        out += [f"- {r.path}:{r.start}-{r.end}" for r in pkg.references]
        out.append("")
    warn = [d for d in pkg.diagnostics if d.level != "info"]
    if warn:
        out.append("_Diagnostics: " + "; ".join(f"{d.engine}: {d.message}" for d in warn) + "_")
    return "\n".join(out).rstrip() + "\n"


def to_json(pkg: ContextPackage) -> str:
    return json.dumps(pkg.to_dict(), indent=2)


def fit_chars(md: str, limit: int) -> str:
    """Trim to `limit` characters on a section boundary so no code fence is left open."""
    if len(md) <= limit:
        return md
    cut = max(md.rfind("\n### ", 0, limit), md.rfind("\n## ", 0, limit))
    return (md[:cut] if cut > 0 else md[:limit]).rstrip() + "\n"
