"""Chat presentation only. Pipeline and fallback app remain unchanged."""
import html
import json
import time
from functools import partial
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from queue import SimpleQueue, Empty

import streamlit as st
import pandas as pd

from answer import load_ledger
from config import DATA_DIR, LESSONS_PATH, SNAPSHOT_PATH, get_settings
from contracts import InspectionGroup, RunResult
from live import evidence_tag, load_catalog
from resolve import auto_scope

st.set_page_config(page_title="LiteralLock NYC", page_icon="🔒", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
:root {--bg-surface:#12161F;--bg-card:#151A24;--border-hairline:rgba(255,255,255,.07);
--brand-blue:#2563EB;--brand-blue-subtle:rgba(37,99,235,.12);--brand-blue-border:rgba(37,99,235,.28);
--text-secondary:#94A3B8;--success:#10B981;--warning:#F59E0B;--error:#EF4444;}
.block-container {max-width:920px;padding-top:2rem;padding-bottom:5rem;}
[data-testid="stChatMessage"] {background:var(--bg-surface);border:1px solid var(--border-hairline);
border-radius:10px;animation:fadeIn 180ms ease-out;}
.understood,.citation-card {background:var(--bg-card);border:1px solid var(--border-hairline);
border-radius:10px;padding:12px;margin:8px 0 12px;}
.understood {font-size:.85rem;color:var(--text-secondary);}
.understood strong {color:#F8FAFC;} .understood ul {margin-bottom:0;}
.answer-claim {font-size:1.06rem;line-height:1.65;margin:12px 0;}
.citation-badge {font-family:monospace;font-size:.75rem;font-weight:600;padding:2px 7px;
background:var(--brand-blue-subtle);border:1px solid var(--brand-blue-border);color:#93c5fd;
border-radius:4px;text-decoration:none;white-space:nowrap;}
.pill {display:inline-block;background:var(--brand-blue-subtle);color:#93c5fd;border:1px solid var(--brand-blue-border);
font-size:.72rem;border-radius:12px;padding:2px 7px;margin:2px 3px 2px 0;overflow-wrap:anywhere;}
.quote {white-space:pre-wrap;overflow-wrap:anywhere;border-left:2px solid var(--brand-blue);
padding-left:10px;color:#CBD5E1;margin:10px 0;font-size:.85rem;}
.verified {color:var(--success);font-size:.72rem;}
.status-badge {display:inline-block;font-size:.72rem;letter-spacing:.08em;border-radius:4px;
padding:4px 8px;color:var(--warning);background:rgba(245,158,11,.10);}
.status-badge.insufficient,.status-badge.conflicting {color:var(--error);background:rgba(239,68,68,.10);}
.status-badge.supported {color:var(--success);background:rgba(16,185,129,.10);}
.reason-rejected {color:var(--error);} .reason-admitted {color:var(--success);}
#MainMenu,footer,header[data-testid="stHeader"] {visibility:hidden;}
[data-testid="stMainMenuButton"] {display:none !important;}
[data-testid="stExpandSidebarButton"] {visibility:visible !important;}
header[data-testid="stHeader"] [data-testid="stSidebarCollapsedControl"],
header[data-testid="stHeader"] button[kind="headerNoPadding"] {visibility:visible;}
[data-testid="stBottomBlockContainer"] {max-width:920px;margin:auto;}
@keyframes fadeIn {from {opacity:0;transform:translateY(6px);} to {opacity:1;transform:translateY(0);}}
@media(prefers-reduced-motion:reduce){[data-testid="stChatMessage"]{animation:none;}}

.ll-hero{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:4px 0 6px}
.ll-brand{display:flex;align-items:center;gap:14px}
.ll-logo{width:46px;height:46px;border-radius:12px;background:linear-gradient(135deg,#3B82F6,#1D4ED8);display:grid;place-items:center;font-size:24px;box-shadow:0 8px 24px rgba(37,99,235,.35)}
.ll-title{font-size:2.1rem;font-weight:800;letter-spacing:-.5px;line-height:1.1;margin:0}
.ll-title span{background:linear-gradient(90deg,#60A5FA,#3B82F6);-webkit-background-clip:text;background-clip:text;color:transparent}
.ll-tag{color:#94A3B8;font-size:.95rem;margin-top:2px}
.ll-board{display:inline-block;text-decoration:none!important;color:#93c5fd!important;background:rgba(37,99,235,.12);border:1px solid rgba(37,99,235,.28);border-radius:10px;padding:8px 14px;font-weight:600;font-size:.9rem;white-space:nowrap}
.ll-board:hover{background:#2563EB;color:#fff!important}
.ll-badges{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 18px}
.ll-badge{font-size:.78rem;color:#cbd5e1;background:#151A24;border:1px solid rgba(255,255,255,.08);border-radius:999px;padding:4px 11px}
.ll-badge b{color:#60A5FA;font-weight:700}
.ll-scene{font-size:.78rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#64748B;margin:10px 0 4px}
div[data-testid="stButton"] button{border-radius:12px!important;border:1px solid rgba(255,255,255,.09)!important;background:#12161F!important;transition:all .18s ease!important;min-height:52px}
div[data-testid="stButton"] button:hover{border-color:rgba(59,130,246,.6)!important;background:#151d2c!important;transform:translateY(-1px);box-shadow:0 6px 18px rgba(37,99,235,.18)}
</style>""", unsafe_allow_html=True)

EXAMPLES = [
    "What violations were recorded at Subway?",
    "What did inspectors find at the Bronx Subway on October 5?",
    "What did inspectors find at the Bronx Subway on June 15, 2025?",
    "Which Queens restaurants had mice or roaches near food preparation?",
]


@st.cache_data
def catalog():
    groups, by_camis = load_catalog()
    return groups, dict(by_camis)


@st.cache_resource
def clients():
    from config import get_es
    from mistralai.client import Mistral
    settings = get_settings()
    es = get_es(settings)
    es.info()  # setup only; excluded from the question's logical request counter
    return settings, es, Mistral(api_key=settings.mistral_api_key, timeout_ms=30000, retry_config=None)


def understood(scope):
    steps = "".join(f"<li>{html.escape(str(step))}</li>" for step in scope.get("steps", []))
    st.markdown(f"<div class='understood'><strong>Understood</strong> · scope resolved by code, not the model<ul>{steps}</ul></div>", unsafe_allow_html=True)


def ledger_panel(ledger):
    st.caption(f"{ledger.get('runs', 0)} runs · {ledger.get('runs_with_rejections', 0)} with rejections · "
               f"{ledger.get('retries', 0)} retries · {ledger.get('retries_improved', 0)} improved retries")
    for code, counts in ledger.get("reasons", {}).items():
        st.caption(f"{code}: seen {counts.get('seen', 0)} · fixed by retry {counts.get('fixed_by_retry', 0)}")
    st.caption("Bounded, one retry max. Rules are code-written; effect not measured. NOT an autonomous self-improving loop.")


def ranking_table(hits):
    st.dataframe([{"rank": h.rank, "CAMIS": h.group.camis, "name": h.group.dba,
                   "date": h.group.inspection_date_key, "type": h.group.inspection_type,
                   "score": h.score} for h in hits], hide_index=True, width="stretch", height=245)


def render_answer(result, turn_id, candidate_card=False):
    r = result
    st.markdown(f"<span class='status-badge {html.escape(r.status)}'>{html.escape(r.status.upper())}</span>", unsafe_allow_html=True)
    if not r.claims:
        if not r.evidence:
            st.markdown("**No findings for this scope in the indexed snapshot.** No restaurant-specific conclusion can be established.")
        else:
            st.markdown("**No validated answer is available.** Admitted source evidence remains available below.")
    for i, claim in enumerate(r.claims, 1):
        anchor = f"citation-{turn_id}-{i}"
        st.markdown(f"<div class='answer-claim'>{html.escape(claim.text)} "
                    f"<a class='citation-badge' href='#{anchor}'>[{i}]</a></div>", unsafe_allow_html=True)
    if r.claims:
        st.caption("Citations · exact excerpts and native inspection scope")
    evidence = {g.group_id: g for g in r.evidence}
    for i, claim in enumerate(r.claims, 1):
        pills = "".join(f"<span class='pill'>{html.escape(part)}</span>" for tag in evidence_tag(claim, evidence)
                        for part in tag.split(" · "))
        quotes = "".join(f"<div class='quote'>{html.escape(ex.quote)}</div>" for ex in claim.excerpts)
        st.markdown(f"<div class='citation-card' id='citation-{turn_id}-{i}'><span class='citation-badge'>[{i}]</span> "
                    f"{pills}{quotes}<div class='verified'>✔ citation in bundle · ✔ verbatim quote · "
                    "✔ CAMIS/date/type in scope · ✔ raw row retained</div></div>", unsafe_allow_html=True)
    first_rejections = [c for c in r.candidates if not c["accepted"] and
                        (c.get("lexical_rank") == 1 or c.get("semantic_rank") == 1)]
    with st.expander("Evidence gate (why candidates were rejected)", expanded=bool(first_rejections) and not candidate_card):
        for c in first_rejections:
            g1 = c["group"] if isinstance(c["group"], InspectionGroup) else InspectionGroup.model_validate(c["group"])
            branches = [b for b, k in (("Lexical", "lexical_rank"), ("Semantic", "semantic_rank")) if c.get(k) == 1]
            why = {"ENTITY_MISMATCH": f"different restaurant (CAMIS {g1.camis} ≠ {r.query.camis})",
                   "DATE_MISMATCH": f"wrong date ({g1.inspection_date_key} ≠ {r.query.inspection_date_key})",
                   "TYPE_MISMATCH": "different inspection type"}.get(c["reason"], c["reason"])
            st.warning(f"{' & '.join(branches)} #1 was {g1.dba} · {getattr(g1, 'address', '') or ''} · "
                       f"{g1.inspection_date_key} → rejected by code: {why}")
        st.caption("⭐ sent to Mistral · ✅ admissible but over cap · ❌ rejected by code")
        bundle = set(evidence)
        rows = []
        for c in r.candidates:
            g = c["group"] if isinstance(c["group"], InspectionGroup) else InspectionGroup.model_validate(c["group"])
            decision = "❌ rejected" if not c["accepted"] else ("⭐ sent to Mistral" if g.group_id in bundle and r.counters.get("mistral_chat") else "✅ admissible")
            rows.append({"decision": decision, "reason": c["reason"], "lex": str(c.get("lexical_rank") or "–"),
                         "sem": str(c.get("semantic_rank") or "–"), "CAMIS": g.camis, "name": g.dba,
                         "date": g.inspection_date_key, "type": g.inspection_type, "source": c.get("provenance")})
        if rows:
            styled = pd.DataFrame(rows).style.apply(
                lambda row: ["color: #EF4444; background-color: rgba(239,68,68,.10)" if row["decision"].startswith("❌")
                             else "color: #10B981; background-color: rgba(16,185,129,.10)"] * len(row), axis=1)
            st.dataframe(styled, hide_index=True, width="stretch")
    with st.expander("Both Elastic rankings"):
        st.caption("Independent lexical and semantic ranks. Weights are heuristics, not confidence.")
        st.markdown("**Lexical**")
        ranking_table(r.lexical)
        st.markdown("**Semantic**")
        ranking_table(r.semantic)
    with st.expander("Self-correction & lessons"):
        attempts = getattr(r, "attempts", None) or []
        for a in attempts:
            if a.get("error"):
                st.error(f"Attempt {a.get('attempt')} → failed: {a['error']}")
            else:
                st.write(f"Attempt {a.get('attempt')} → proposed {a.get('proposed', 0)} → retained {a.get('retained', 0)}")
                for code in a.get("removed_reasons", []):
                    st.error(f"Removed: {code}")
        if len(attempts) == 1 and not attempts[0].get("removed_reasons") and not attempts[0].get("error"):
            st.success("First answer passed every check → no retry needed")
        elif len(attempts) > 1:
            st.write("Self-correction: one retry with the validator's feedback")
        elif r.counters.get("mistral_chat"):
            st.caption("This recording has no attempts timeline.")
        for event in getattr(r, "frontend_retry_events", []) or []:
            for removed in event.get("removed", []):
                st.error(removed)
        for gap in r.gaps:
            if "removed claim" in gap:
                st.error(gap)
        for lesson in getattr(r, "lessons_applied", []) or []:
            st.markdown(f"- {lesson}")
        if getattr(r, "ledger", None):
            st.caption("Ledger recorded at this run")
            ledger_panel(r.ledger)
        else:
            st.caption("No ledger recorded with this run. Live ledger is in the sidebar.")
    with st.expander("Gaps & limits"):
        for gap in r.gaps:
            st.write(gap)
        st.caption("Bounded NYC snapshot, not citywide or current safety. Quote checks do not prove semantic entailment.")
        for g in r.evidence:
            st.caption(f"{g.group_id} · coverage: {g.coverage}")
            st.code(g.body, language=None)
    with st.expander("Budget"):
        st.caption(f"Elastic: {r.counters.get('elastic_logical', 0)} logical requests · "
                   f"Recovery: {r.counters.get('recovery_rounds', 0)}/1 rounds, {r.counters.get('recovery_logical', 0)}/2 extra requests · "
                   f"Mistral: {r.counters.get('mistral_chat', 0)}/2 chat calls · {r.timings.get('total', 0)} seconds")
        st.json(getattr(r, "integration", {}), expanded=False)


def submit(question, scope=None):
    st.session_state["chat"].append({"role": "user", "question": question})
    st.session_state["chat"].append({"role": "assistant", "question": question,
                                     "scope": scope or auto_scope(question, groups), "pending": True})


def save_run(r):
    directory = DATA_DIR / "live_runs"
    directory.mkdir(exist_ok=True)
    path = directory / f"chat-{time.strftime('%H%M%S')}.json"
    while True:
        try:
            with path.open("x") as out:
                out.write(r.model_dump_json(indent=2))
            return str(path.relative_to(DATA_DIR))
        except FileExistsError:
            path = directory / f"chat-{time.strftime('%H%M%S')}-{time.time_ns()}.json"


def execute_candidates(turn):
    """Run independent scopes concurrently; workers never call Streamlit APIs."""
    from demo import run
    from retrieval import lexical_search, semantic_search
    from scope import make_query
    turn["pending"] = False
    candidates = list(turn["scope"]["resolution"]["candidates"].items())[:3]
    turn["candidate_results"] = []
    updates = SimpleQueue()
    try:
        settings, es, mistral = clients()
    except Exception as exc:
        turn["error"] = f"Live services unavailable ({type(exc).__name__}). Use recorded replay."
        return

    def one(camis, group):
        dates = sorted({g["inspection_date_key"] for g in groups if g["camis"] == camis and g.get("inspection_date_key")}, reverse=True)
        trace, retries = [], []
        query = make_query(turn["question"], camis, dates[0], None, None)
        def on_step(event, **d):
            if event == "branch":
                line = f"{d['name']}: {len(d['hits'])} groups · {d['seconds']:.2f}s"
            elif event == "gate":
                line = f"Gate: {sum(c['accepted'] for c in d['candidates'])} admissible, evidence locked to CAMIS {camis}"
            elif event == "recovery":
                line = "Recovery failed; absence unproven" if d["total"] is None else f"Exact recovery: {d['total']} indexed groups"
            elif event == "bundle":
                line = f"Bundle: {len(d['evidence'])} groups" + (" → Mistral" if d["will_answer"] else " → no chat call")
            elif event == "retry":
                line = "Self-correction: one retry with validator feedback"
                retries.append({"reasons": d.get("reasons", []), "removed": d.get("removed", [])})
                for removed in d.get("removed", []):
                    updates.put((camis, "error", removed))
            elif event == "done":
                line = f"Validation: {len(d['result'].claims)} retained claims"
            else:
                return
            trace.append(line)
            updates.put((camis, "line", line))
        r = run(query, partial(lexical_search, es=es, index=settings.elasticsearch_index),
                partial(semantic_search, es=es, index=settings.elasticsearch_index), es, settings.elasticsearch_index,
                mistral, settings.mistral_chat_model, snap, on_step=on_step, lessons_path=LESSONS_PATH)
        r.frontend_trace = trace
        r.frontend_retry_events = retries
        return {"camis": camis, "name": group["dba"], "address": group.get("address", ""), "date": dates[0],
                "result_json": r.model_dump_json(), "saved_path": save_run(r)}

    with st.status("Evidence trace · separate restaurant scopes", expanded=True) as status:
        st.caption("Concurrent, at most three restaurants. Each has independent evidence and request budgets.")
        for camis, _ in candidates:
            st.write(f"CAMIS {camis} → own latest snapshot date; evidence never merged")
        with ThreadPoolExecutor(max_workers=min(3, len(candidates))) as executor:
            futures = {executor.submit(one, camis, g): (camis, g) for camis, g in candidates}
            remaining = set(futures)
            while remaining or not updates.empty():
                completed, remaining = wait(remaining, timeout=.1, return_when=FIRST_COMPLETED)
                while True:
                    try:
                        camis, kind, line = updates.get_nowait()
                    except Empty:
                        break
                    (st.error if kind == "error" else st.write)(f"CAMIS {camis} · {line}")
                for future in completed:
                    camis, g = futures[future]
                    try:
                        item = future.result()
                    except Exception as exc:
                        item = {"camis": camis, "name": g["dba"], "address": g.get("address", ""),
                                "error": f"Run unavailable ({type(exc).__name__}); no finding established for this identity."}
                        st.error(f"CAMIS {camis}: {item['error']}")
                    turn["candidate_results"].append(item)
        turn["candidate_results"].sort(key=lambda item: item["camis"])
        status.update(label="Evidence trace · separate restaurant scopes", state="complete", expanded=False)


def execute(turn):
    from demo import run
    from retrieval import lexical_search, semantic_search
    from scope import make_query
    turn["pending"] = False  # rerendering must never repeat a request
    scope = turn["scope"]
    events, retry_events = [], []
    try:
        query = make_query(turn["question"], scope.get("camis"), scope.get("date"), scope.get("type"), scope.get("borough"))
        settings, es, mistral = clients()
        with st.status("Evidence trace", expanded=True) as status:
            st.caption("Code actions and evidence checks, not model reasoning.")
            def on_step(event, **d):
                if event == "branch":
                    line = f"Elastic {d['name']}: {len(d['hits'])} groups · {d['seconds']:.2f}s"
                elif event == "gate":
                    count = sum(c["accepted"] for c in d["candidates"])
                    line = f"Evidence gate: {count} admissible, {len(d['candidates']) - count} rejected by code"
                elif event == "recovery":
                    line = "Exact recovery failed; absence unproven" if d["total"] is None else f"Exact recovery: {d['total']} matching indexed groups"
                elif event == "bundle":
                    line = f"Bundle: {len(d['evidence'])} groups → Mistral" if d["will_answer"] else "No admissible evidence → no Mistral call"
                elif event == "retry":
                    line = "Self-correction: one retry with the validator's feedback"
                    retry_events.append({"reasons": d.get("reasons", []), "removed": d.get("removed", [])})
                    for removed in d.get("removed", []):
                        st.error(removed)
                elif event == "done":
                    line = f"Validation: {len(d['result'].claims)} retained claims · {d['result'].timings.get('total')}s"
                else:
                    return
                events.append(line)
                st.write(line)
            r = run(query, partial(lexical_search, es=es, index=settings.elasticsearch_index),
                    partial(semantic_search, es=es, index=settings.elasticsearch_index), es, settings.elasticsearch_index,
                    mistral, settings.mistral_chat_model, snap, on_step=on_step, lessons_path=LESSONS_PATH)
            status.update(label="Evidence trace", state="complete", expanded=False)
        r.frontend_trace = events
        r.frontend_retry_events = retry_events
        turn["result_json"] = r.model_dump_json()
        turn["saved_path"] = save_run(r)
    except Exception as exc:
        turn["error"] = f"Run unavailable ({type(exc).__name__}). Use Replay or the app.py/live.py fallback."


def render_turn(turn, turn_id, replay=False):
    with st.chat_message(turn["role"]):
        if turn["role"] == "user":
            st.write(turn["question"])
            return
        scope = turn.get("scope", {})
        if scope.get("mode") == "ambiguous":
            understood({"steps": [step for step in scope.get("steps", []) if "no restaurant-specific answer" not in step]
                                  + ["The name does not select one identity → answer each candidate independently, at most three."]})
        else:
            understood(scope)
        ran_now = False
        if scope.get("mode") == "ambiguous":
            candidates = scope["resolution"]["candidates"]
            names = sorted({g["dba"] for g in candidates.values()})
            st.markdown(f"**Your question matches {len(candidates)} different restaurants named {', '.join(names)} — "
                        "answered separately, evidence never merged**")
            if len(candidates) > 3:
                st.warning("Bounded to three candidates. Other matching restaurants are not answered in this turn.")
            if turn.get("pending"):
                execute_candidates(turn)
            for index, item in enumerate(turn.get("candidate_results", [])):
                with st.container(border=True):
                    st.markdown(f"**{item['name']} · CAMIS `{item['camis']}`**")
                    st.caption(item.get("address", ""))
                    if item.get("error"):
                        st.error(item["error"])
                    elif item.get("result_json"):
                        st.caption(f"{item['date']} — latest date in this snapshot for this CAMIS, not necessarily the latest city inspection")
                        render_answer(RunResult.model_validate_json(item["result_json"]), f"{turn_id}-candidate-{index}", candidate_card=True)
                        st.caption(f"Saved run: data/{item['saved_path']}")
            if turn.get("error"):
                st.error(turn["error"])
            st.markdown("**Add a street, borough or ZIP to focus on one.**")
            return
        if turn.get("pending"):
            execute(turn)
            ran_now = True
        if turn.get("error"):
            st.error(turn["error"])
        if turn.get("result_json"):
            r = RunResult.model_validate_json(turn["result_json"])
            if replay:
                st.info("Recorded run — no retrieval or inference now")
            if not ran_now:
                with st.status("Evidence trace", state="complete", expanded=False):
                    st.caption("Recorded code actions; opening this trace makes no calls.")
                    for line in getattr(r, "frontend_trace", []) or []:
                        st.write(line)
                    if not getattr(r, "frontend_trace", None):
                        st.caption("This recording has no streaming event log. Inspect rankings and gate below.")
            render_answer(r, turn_id)
            if turn.get("saved_path"):
                st.caption(f"Saved run: data/{turn['saved_path']}")
            if r.query.camis and not replay:
                with st.popover("Refine scope"):
                    dates = sorted({g["inspection_date_key"] for g in by_camis[r.query.camis] if g.get("inspection_date_key")}, reverse=True)
                    date = st.selectbox("Inspection date", dates, key=f"refine-date-{turn_id}")
                    types = sorted({g["inspection_type"] for g in by_camis[r.query.camis] if g["inspection_date_key"] == date and g.get("inspection_type")})
                    kind = st.selectbox("Inspection type", ["(any)"] + types, key=f"refine-type-{turn_id}")
                    if st.button("Re-run", key=f"refine-run-{turn_id}"):
                        narrowed = None if kind == "(any)" else kind
                        submit(turn["question"], {"mode": "entity", "camis": r.query.camis, "date": date, "type": narrowed, "borough": None,
                                                 "steps": [f"You refined CAMIS {r.query.camis} to {date}; type {narrowed or 'any'}", "This is a new turn; the earlier result is unchanged."]})
                        st.rerun()


groups, by_camis = catalog()
snap = json.loads(SNAPSHOT_PATH.read_text()) if SNAPSHOT_PATH.exists() else {"coverage_notes": "Manifest unavailable"}
settings = get_settings()
st.session_state.setdefault("chat", [])

with st.sidebar:
    st.header("Data & sponsors")
    st.caption(f"NYC DOHMH · 43nn-pn8j · {snap.get('snapshot_id')}")
    st.caption(f"{snap.get('unique_raw_row_count')} unique rows · {snap.get('unique_camis_count')} restaurants · {snap.get('inspection_group_count')} groups")
    st.caption(f"Elastic: {settings.elasticsearch_index} · BM25 + semantic_text")
    st.caption(f"Mistral: {settings.mistral_embed_model} embeddings · {settings.mistral_chat_model} chat")
    st.caption("Sampled historical records, not citywide coverage or current safety.")
    mode = st.radio("Mode", ["Live", "Replay"], key="chat_mode")
    with st.expander("Live local ledger"):
        ledger_panel(load_ledger(LESSONS_PATH))

BOARD_URL = "http://127.0.0.1:8503/landing.html"
st.markdown(f"""<div class="ll-hero"><div class="ll-brand"><div class="ll-logo">🔒</div><div>
<div class="ll-title">Literal<span>Lock</span> NYC</div>
<div class="ll-tag">Same name. Different restaurant. Show the evidence.</div></div></div>
<a class="ll-board" href="{BOARD_URL}" target="_self">▦ Restaurant board</a></div>
<div class="ll-badges"><span class="ll-badge"><b>Elastic</b> BM25 + semantic_text</span>
<span class="ll-badge"><b>Mistral</b> mistral-embed · structured answer</span>
<span class="ll-badge"><b>NYC Open Data</b> 43nn-pn8j · 70 restaurants</span>
<span class="ll-badge"><b>Code-enforced</b> CAMIS · date · citations</span></div>""", unsafe_allow_html=True)

landing_question = st.query_params.get("q", "").strip()
if mode == "Live" and landing_question and st.session_state.get("landing_question_consumed") != landing_question:
    # Pre-fill only: the user reviews/edits the board's question and decides when to run it.
    st.session_state["landing_question_consumed"] = landing_question
    st.session_state["landing_prefill"] = landing_question
    st.session_state["landing_edit"] = landing_question

if mode == "Live" and st.session_state.get("landing_prefill"):
    with st.container(border=True):
        st.markdown("**▦ Question from the restaurant board** — edit it or ask as is. Nothing has run yet.")
        edited = st.text_input("Question", key="landing_edit", label_visibility="collapsed")
        c1, c2, _ = st.columns([1.3, 1, 3])
        if c1.button("▶ Ask LiteralLock", key="landing_ask", type="primary", width="stretch") and edited.strip():
            st.session_state["landing_prefill"] = None
            submit(edited.strip())
            st.rerun()
        if c2.button("✕ Dismiss", key="landing_dismiss", width="stretch"):
            st.session_state["landing_prefill"] = None
            st.rerun()

if mode == "Replay":
    paths = sorted(set(DATA_DIR.glob("*_run.json")) | set((DATA_DIR / "live_runs").glob("*.json")))
    if not paths:
        st.warning("No recorded runs available.")
        st.stop()
    index = next((i for i, p in enumerate(paths) if p.name == "subway_exact_run.json"), 0)
    choice = st.selectbox("Recorded run", paths, index=index, key="chat_recorded_run", format_func=lambda p: str(p.relative_to(DATA_DIR)))
    r = RunResult.model_validate_json(choice.read_text())
    scope = {"steps": [f"Recorded CAMIS: {r.query.camis or 'conceptual'}", f"Recorded date: {r.query.inspection_date_key or 'per source'}", f"Borough: {r.query.borough or 'not narrowed'}"]}
    render_turn({"role": "user", "question": r.query.question}, "replay-user", replay=True)
    render_turn({"role": "assistant", "scope": scope, "result_json": r.model_dump_json()}, "replay", replay=True)
    st.stop()

if not st.session_state["chat"]:
    st.markdown("Ask about recorded inspections — identity, date and evidence are checked by code, not the model.")
    scene_labels = ["① Vague name · no guessing", "② Identity + date from your words",
                    "③ Not in the data · no invented answer", "④ Conceptual · semantic-heavy"]
    cols = st.columns(2)
    for i, example in enumerate(EXAMPLES):
        with cols[i % 2]:
            st.markdown(f"<div class='ll-scene'>{scene_labels[i]}</div>", unsafe_allow_html=True)
            if st.button(example, key=f"example-{i}", width="stretch"):
                submit(example)
                st.rerun()

for i, turn in enumerate(st.session_state["chat"]):
    render_turn(turn, i)

if question := st.chat_input("Ask about NYC restaurant inspections…"):
    submit(question)
    st.rerun()
