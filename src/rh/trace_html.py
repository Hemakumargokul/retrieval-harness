"""Render a trace dict (see rh/trace.py) as one self-contained HTML page: no JavaScript,
no external files, light and dark mode. Every value from the repo is HTML-escaped."""
from __future__ import annotations

import time
from html import escape as _e

CSS = """
:root{--bg:#fff;--fg:#1d2330;--mut:#667085;--line:#e3e7ee;--card:#f7f9fc;--ok:#0b7a43;--okbg:#e4f5ec;
--bad:#b42318;--badbg:#fdecea;--warn:#8a5a00;--warnbg:#fff4d6;--rg:#1d5fd1;--cg:#7a3bd1;--cbm:#0b7a43;--bar:#2f6fed}
@media (prefers-color-scheme:dark){:root{--bg:#10141c;--fg:#e6eaf2;--mut:#98a2b3;--line:#273042;--card:#171d29;
--ok:#4ade80;--okbg:#12301f;--bad:#f87171;--badbg:#3a1a1a;--warn:#fbbf24;--warnbg:#33290d;--rg:#7aa7ff;--cg:#c4a1ff;--cbm:#4ade80;--bar:#6b9bff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0}.mut{color:var(--mut)}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.task{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:10px 0;white-space:pre-wrap}
.flow{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 22px}
.flow a{display:block;text-decoration:none;color:inherit;border:1px solid var(--line);background:var(--card);border-radius:8px;padding:6px 10px;min-width:118px}
.flow a b{display:block;font-size:12px;color:var(--mut);font-weight:600}.flow a span{font-size:14px}
.flow a.off{opacity:.55}.flow a.warn{border-color:var(--warn)}
details.stage{border:1px solid var(--line);border-radius:10px;margin:12px 0;background:var(--bg)}
details.stage>summary{cursor:pointer;padding:10px 14px;list-style:none;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
details.stage>summary::-webkit-details-marker{display:none}
details.stage>summary .n{background:var(--bar);color:#fff;border-radius:50%;width:22px;height:22px;display:inline-flex;align-items:center;justify-content:center;font-size:12px}
details.stage>.body{padding:2px 14px 14px}
table{border-collapse:collapse;width:100%;margin:8px 0}th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--mut);font-weight:600}td.num,th.num{text-align:right;white-space:nowrap}
.badge{display:inline-block;border-radius:5px;padding:0 6px;font-size:12px;border:1px solid currentColor;margin-right:3px}
.b-ripgrep{color:var(--rg)}.b-colgrep{color:var(--cg)}.b-cbm{color:var(--cbm)}.b-role{color:var(--mut)}
.keep{background:var(--okbg)}.drop{background:var(--badbg)}.drop td{text-decoration:line-through;text-decoration-color:var(--bad)}
.tag{border-radius:5px;padding:0 6px;font-size:12px;white-space:nowrap}.t-ok{background:var(--okbg);color:var(--ok)}.t-bad{background:var(--badbg);color:var(--bad)}.t-warn{background:var(--warnbg);color:var(--warn)}
.bar{height:12px;background:var(--line);border-radius:6px;overflow:hidden;margin:6px 0}.bar i{display:block;height:100%;background:var(--bar)}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;max-height:420px;white-space:pre-wrap}
.kv{display:grid;grid-template-columns:150px 1fr;gap:2px 12px}.kv div:nth-child(odd){color:var(--mut)}
ul.clean{margin:4px 0;padding-left:18px}
@media (max-width:600px){.kv{grid-template-columns:1fr}.flow a{min-width:0;flex:1 1 45%}}
"""

MAX_ROWS = 25


def e(x) -> str:
    return _e(str(x), quote=True)


def badges(sources) -> str:
    return "".join(f'<span class="badge b-{e(s)}">{e(s)}</span>' for s in sources)


def rng(d: dict) -> str:
    return f'<code>{e(d["path"])}:{d["start"]}-{d["end"]}</code>'


def table(head: list[tuple[str, str]], rows: list[tuple[str, list[str]]], more: int = 0) -> str:
    """head: (label, css class); rows: (row class, cells as ready-made HTML)."""
    out = ["<table><thead><tr>" + "".join(f'<th class="{c}">{e(h)}</th>' for h, c in head) + "</tr></thead><tbody>"]
    for cls, cells in rows:
        out.append(f'<tr class="{cls}">' + "".join(
            f'<td class="{head[i][1]}">{c}</td>' for i, c in enumerate(cells)) + "</tr>")
    out.append("</tbody></table>")
    if more > 0:
        out.append(f'<div class="mut">… and {more} more</div>')
    return "".join(out)


def ev_rows(evs: list[dict], final: list[dict] | None = None, limit: int = MAX_ROWS):
    rows = []
    for d in evs[:limit]:
        used = final is not None and any(f["path"] == d["path"] and f["start"] <= d["end"] and d["start"] <= f["end"]
                                         for f in final)
        mark = '<span class="tag t-ok">in output</span>' if used else ""
        rows.append(("keep" if used else "", [
            str(d.get("rank", "")), rng(d), badges(d["sources"]), f'<span class="badge b-role">{e(d["role"])}</span>',
            e(d.get("symbol") or ""), f'{d["score"]:.4f}' if d.get("score") else "—", mark]))
    return rows, max(0, len(evs) - limit)


EV_HEAD = [("#", "num"), ("range", ""), ("found by", ""), ("role", ""), ("symbol", ""), ("score", "num"), ("", "")]


def stage(n: int, sid: str, title: str, summary: str, body: str, open_: bool = True) -> str:
    # Always open: the flow chips are plain links, and a link cannot open a closed <details> without JS.
    return (f'<details class="stage" id="{sid}" open><summary><span class="n">{n}</span>'
            f'<h2>{e(title)}</h2><span class="mut">{summary}</span></summary><div class="body">{body}</div></details>')


def render_html(data: dict) -> str:
    st = data.get("stages", {})
    pkg = data.get("package", {})
    cfg = data.get("config", {})
    final_items = (st.get("budget") or {}).get("kept", [])
    timings = pkg.get("timings_ms", {})

    an = st.get("analyze", {})
    engines = st.get("engines", {}).get("engines", [])
    search = st.get("search", {}).get("engines", [])
    fuse = st.get("fuse", {})
    exp = st.get("expand", {})
    exc = st.get("excerpt", {})
    jud = st.get("judge", {})
    bud = st.get("budget", {})

    flow, cards = [], []

    def chip(sid, label, value, cls=""):
        flow.append(f'<a href="#{sid}" class="{cls}"><b>{e(label)}</b><span>{value}</span></a>')

    if st.get("gate", {}).get("skipped"):
        body = '<p>The prompt was skipped by the gate (too short, chit-chat, or a slash command). Nothing was retrieved.</p>'
        return _page(data, '<div class="flow"><a href="#gate"><b>1 Gate</b><span>skipped</span></a></div>' +
                     stage(1, "gate", "Gate", "skipped", body, True))

    # 1 analyze
    kv = [("anchors (exact search)", " ".join(f"<code>{e(a)}</code>" for a in an.get("anchors", [])) or "<i>none</i>"),
          ("identifiers", e(", ".join(an.get("identifiers", [])) or "—")),
          ("quoted strings", e(", ".join(an.get("quoted", [])) or "—")),
          ("file paths", e(", ".join(an.get("paths", [])) or "—")),
          ("error lines", e("; ".join(an.get("errors", [])) or "—")),
          ("cross-file wording", "yes" if an.get("cross_file") else "no"),
          ("semantic query", e(an.get("semantic_query", "")))]
    body = '<div class="kv">' + "".join(f"<div>{k}</div><div>{v}</div>" for k, v in kv) + "</div>"
    n_anch = len(an.get("anchors", []))
    cards.append(stage(1, "s-analyze", "Task analysis", f"{n_anch} exact anchor(s); cross-file: {'yes' if an.get('cross_file') else 'no'}", body, True))
    chip("s-analyze", "1 Analyze", f"{n_anch} anchors")

    # 2 engines + search
    rows = [("", [e(x["name"]), e(x["role"]), f'<span class="tag {"t-ok" if x["status"] == "ok" else "t-warn"}">{e(x["status"])}</span>']) for x in engines]
    body = table([("engine", ""), ("role", ""), ("status", "")], rows)
    parts = []
    for s in search:
        hits = s.get("hits", [])
        state = '<span class="tag t-bad">timed out</span>' if s.get("timed_out") else f'{len(hits)} hits, {s.get("ms")} ms'
        r, more = ev_rows(hits, final_items, 12)
        qs = ", ".join(f"<code>{e(q[:90])}</code>" for q in s.get("queries", []))
        parts.append(f'<h3 style="font-size:14px;margin:14px 0 0">{badges([s["name"]])} {state}</h3>'
                     f'<div class="mut">query: {qs}</div>' + (table(EV_HEAD, r, more) if hits else ""))
    body += "".join(parts)
    counts = " · ".join(f'{e(s["name"])} {len(s.get("hits", []))}' for s in search) or "no engine ran"
    cards.append(stage(2, "s-search", "Search (raw, per engine)", counts, body, True))
    chip("s-search", "2 Search", counts)

    # 3 fuse
    r, more = ev_rows(fuse.get("ranges", []), final_items, MAX_ROWS)
    body = ('<p class="mut">Scores are reciprocal-rank-fusion sums; ranges within 3 lines are merged. '
            '“in output” marks ranges that overlap an excerpt in the final context.</p>' + table(EV_HEAD, r, more + max(0, fuse.get("total", 0) - len(fuse.get("ranges", [])))))
    cards.append(stage(3, "s-fuse", "Fusion", f'{fuse.get("total", 0)} merged ranges', body))
    chip("s-fuse", "3 Fuse", f'{fuse.get("total", 0)} ranges')

    # 4 expand
    ran = exp.get("decision") == "ran"
    body = f'<p>{e(exp.get("decision", ""))}' + (f' — because {e(exp["reason"])}' if exp.get("reason") else "") + "</p>"
    if ran:
        body += f'<p>Symbols looked up: {" ".join("<code>" + e(s) + "</code>" for s in exp.get("symbols", [])) or "none"}</p>'
        if exp.get("edges"):
            body += "<p>Call edges found:</p><ul class='clean'>" + "".join(
                f"<li><code>{e(a)}</code> → <code>{e(b)}</code> <span class='mut'>({e(k)})</span></li>" for a, b, k in exp["edges"]) + "</ul>"
        new = exp.get("new", [])
        if new:
            r, more = ev_rows(new, final_items)
            body += "<p>New excerpts added from the graph:</p>" + table(EV_HEAD, r, more)
        else:
            body += '<p class="mut">The graph added no excerpts that were not already found by search.</p>'
    cards.append(stage(4, "s-expand", "Graph expansion", e(exp.get("decision", "")[:60]) + (f' · {len(exp.get("edges", []))} edges' if ran else ""), body, ran))
    chip("s-expand", "4 Expand", f'{len(exp.get("edges", []))} edges' if ran else "not run", "" if ran else "off")

    # 5 excerpt
    items = exc.get("items", [])
    rows = []
    for c in exc.get("candidates", [])[:MAX_ROWS]:
        cover = next((i for i in items if i["path"] == c["path"] and i["start"] <= c["end"] and c["start"] <= i["end"]), None)
        if cover is None:
            note, cls = '<span class="tag t-bad">dropped (file missing or range invalid)</span>', "drop"
            out = ""
        else:
            same = (cover["start"], cover["end"]) == (c["start"], c["end"])
            grew = (c["end"] - c["start"] + 1, cover["end"] - cover["start"] + 1)
            note = "unchanged" if same else f"widened {grew[0]} → {grew[1]} lines"
            cls, out = "", rng(cover) + f' <span class="mut">{cover["tokens"]} tok</span>'
        rows.append((cls, [rng(c), badges(c["sources"]), out, note]))
    body = ('<p class="mut">Each retrieved range is re-read from disk and widened to the enclosing function '
            '(Python) or padded with context lines. Overlapping excerpts are merged.</p>' +
            table([("retrieved range", ""), ("found by", ""), ("excerpt", ""), ("change", "")], rows,
                  max(0, len(exc.get("candidates", [])) - MAX_ROWS)))
    cards.append(stage(5, "s-excerpt", "Excerpts", f'{len(exc.get("candidates", []))} ranges → {len(items)} excerpts', body))
    chip("s-excerpt", "5 Excerpt", f'{len(items)} excerpts')

    # 6 judge
    dec = jud.get("decision", "off")
    body = f"<p>{e(dec)}</p>"
    jchip, jcls = "off", "off"
    if jud.get("kept") is not None and dec not in ("off",):
        pinned = set(jud.get("pinned", []))
        allj = [("keep", x) for x in jud.get("kept", [])] + [("drop", x) for x in jud.get("dropped", [])]
        allj.sort(key=lambda t: (t[1]["path"], t[1]["start"]))
        rows = [(c, [rng(x), badges(x["sources"]), f'<span class="badge b-role">{e(x["role"])}</span>', str(x["tokens"]),
                     ('<span class="tag t-ok">kept (pinned)</span>' if f"{x['path']}:{x['start']}-{x['end']}" in pinned
                      else '<span class="tag t-ok">kept</span>') if c == "keep" else '<span class="tag t-bad">dropped</span>'])
                for c, x in allj]
        body += table([("excerpt", ""), ("found by", ""), ("role", ""), ("tokens", "num"), ("judge", "")], rows)
        if jud.get("model"):
            body += (f'<details><summary class="mut">model <code>{e(jud["model"])}</code> — prompt and raw answer</summary>'
                     f'<p class="mut">Prompt sent:</p><pre>{e(jud.get("prompt", ""))}</pre>'
                     f'<p class="mut">Raw answer:</p><pre>{e(jud.get("raw", jud.get("error", "")))}</pre></details>')
        if jud.get("model") and dec == "ran":
            jchip, jcls = f'kept {len(jud["kept"])}/{len(jud["kept"]) + len(jud["dropped"])}', ""
        elif jud.get("model"):
            jchip, jcls = "kept all (failed)", "warn"
        else:
            jchip = "skipped"
    cards.append(stage(6, "s-judge", "LLM judge", e(dec[:70]), body, dec == "ran"))
    chip("s-judge", "6 Judge", e(jchip), jcls)

    # 7 budget
    used, usable = bud.get("used", 0), max(1, bud.get("usable", 1))
    pct = min(100, int(100 * used / usable))
    body = (f'<p>{used} of {usable} usable tokens ({bud.get("budget")} budget, 10% reserved for the summary).</p>'
            f'<div class="bar"><i style="width:{pct}%"></i></div>')
    if not bud.get("overflow"):
        body += '<p class="mut">Everything fit, so the budget removed nothing. It orders by role (code, then tests, then callers, then docs) and only cuts when the total is too large.</p>'
    rows = [("keep", [rng(x), badges(x["sources"]), f'<span class="badge b-role">{e(x["role"])}</span>', str(x["tokens"]), '<span class="tag t-ok">in output</span>']) for x in bud.get("kept", [])]
    rows += [("drop", [rng(x), badges(x["sources"]), f'<span class="badge b-role">{e(x["role"])}</span>', str(x["tokens"]), '<span class="tag t-bad">over budget</span>']) for x in bud.get("overflow", [])]
    body += table([("excerpt", ""), ("found by", ""), ("role", ""), ("tokens", "num"), ("", "")], rows)
    cards.append(stage(7, "s-budget", "Token budget", f"{used}/{usable} tokens, {len(bud.get('overflow', []))} over budget", body))
    chip("s-budget", "7 Budget", f"{used}/{usable} tok")

    # 8 output
    diags = pkg.get("diagnostics", [])
    body = ""
    if diags:
        body += "<ul class='clean'>" + "".join(
            f'<li><span class="tag {"t-warn" if d["level"] != "info" else "t-ok"}">{e(d["level"])}</span> <b>{e(d["engine"])}</b>: {e(d["message"])}</li>'
            for d in diags) + "</ul>"
    body += f'<p class="mut">This is exactly what the agent receives:</p><pre>{e(data.get("output_markdown", ""))}</pre>'
    cards.append(stage(8, "s-output", "Final output", f'{pkg.get("token_estimate", 0)} tokens, {len(pkg.get("items", []))} excerpts', body, True))
    warn = any(d["level"] != "info" for d in diags)
    chip("s-output", "8 Output", f'{pkg.get("token_estimate", 0)} tok', "warn" if warn else "")

    tm = " · ".join(f"{e(k)} {v} ms" for k, v in timings.items())
    head = f'<div class="flow">{"".join(flow)}</div><p class="mut mono">timings: {tm}</p>'
    return _page(data, head + "".join(cards))


def _page(data: dict, inner: str) -> str:
    pkg = data.get("package", {})
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data.get("started", time.time())))
    task = pkg.get("task") or (data.get("stages", {}).get("gate", {}).get("task", ""))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Retrieval trace</title>'
            f'<style>{CSS}</style></head><body><main><h1>Retrieval trace</h1>'
            f'<div class="mut">{e(started)} · repo <code>{e(data.get("repo") or "")}</code> · profile {e(pkg.get("profile", ""))}</div>'
            f'<div class="task">{e(task)}</div>{inner}</main></body></html>')
