"""LiteralLock NYC web demo (Streamlit). Same pipeline as live.py / demo.py; display only.

Run:  .venv/bin/streamlit run app.py
Replay mode makes no network calls (stage fallback).
"""
import html
import json
import time
from collections import Counter
from functools import partial
from pathlib import Path

import streamlit as st

from config import DATA_DIR, SNAPSHOT_PATH, LESSONS_PATH, get_settings
from answer import load_ledger
from resolve import resolve
from contracts import InspectionGroup, RunResult
from live import evidence_tag, load_catalog, name_matches

st.set_page_config(page_title="LiteralLock NYC", page_icon="🔒", layout="wide")

st.markdown("""
<style>
.tag {display:inline-block; padding:2px 8px; margin:2px 4px 2px 0; border-radius:10px;
      font-size:0.80rem; background:#e8f0fe; color:#1a3d7c; border:1px solid #c6d6f5;}
.tag.code {background:#fdecea; color:#8a1c12; border-color:#f5c2bc;}
.tag.row {background:#f1f3f4; color:#444; border-color:#ddd; font-family:monospace;}
.check {color:#137333; font-size:0.82rem;}
.claim {font-size:1.05rem; font-weight:600; margin-top:0.6rem;}
.quote {border-left:3px solid #bbb; padding-left:10px; color:#555; font-style:italic; margin:4px 0 2px 0;}
.badge {padding:3px 10px; border-radius:6px; font-weight:700; color:white;}
.step {font-weight:700; color:#0b5394;}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ cached resources

@st.cache_resource(show_spinner="Connecting to Elastic…")
def clients():
    from config import get_es
    s = get_settings()
    es = get_es(s)
    t0 = time.perf_counter()
    es.info()  # warm HTTPS connection; setup only, not counted in run budgets
    warm = time.perf_counter() - t0
    from mistralai.client import Mistral
    mistral = Mistral(api_key=s.mistral_api_key, timeout_ms=30000, retry_config=None)
    return s, es, mistral, warm


@st.cache_data
def catalog():
    groups, by_camis = load_catalog()
    return groups, dict(by_camis)


def snapshot():
    return json.loads(SNAPSHOT_PATH.read_text()) if SNAPSHOT_PATH.exists() else {}


def as_group(g):
    return g if isinstance(g, InspectionGroup) else InspectionGroup.model_validate(g)


# ------------------------------------------------------------------ rendering from a RunResult

def scope_text(q):
    if q.camis:
        return (f"**Exact-entity profile** · CAMIS `{q.camis}` · date `{q.inspection_date_key}` · "
                f"type `{q.inspection_type or 'any'}` · lexical **{q.lexical_weight}** / semantic **{q.semantic_weight}**")
    return (f"**Conceptual profile** · no restaurant selected" + (f" · borough `{q.borough}`" if q.borough else "")
            + f" · lexical **{q.lexical_weight}** / semantic **{q.semantic_weight}**")


def branch_table(hits, q):
    rows = []
    for h in hits:
        g = h.group
        rows.append({
            "rank": h.rank,
            "CAMIS": g.camis + ("" if not q.camis else (" ✅" if g.camis == q.camis else " ❌")),
            "restaurant": g.dba,
            "date": (g.inspection_date_key or "unknown") + ("" if not q.camis else (" ✅" if g.inspection_date_key == q.inspection_date_key else " ❌")),
            "type": g.inspection_type,
            "score": round(h.score or 0, 3),
        })
    st.dataframe(rows, hide_index=True, width="stretch", height=245)


def gate_table(candidates, q, bundle_ids):
    rows = []
    for c in candidates:
        g = as_group(c["group"])
        why = c["reason"]
        if not c["accepted"] and q.camis:
            if why == "ENTITY_MISMATCH":
                why += f" ({g.camis} ≠ {q.camis})"
            elif why == "DATE_MISMATCH":
                why += f" ({g.inspection_date_key} ≠ {q.inspection_date_key})"
        decision = ("⭐ ADMIT → Mistral" if g.group_id in bundle_ids else "✅ ADMIT (over 4-group cap)") if c["accepted"] else "❌ REJECT"
        rows.append({"decision": decision, "reason": why, "lex": str(c.get("lexical_rank", "–")),
                     "sem": str(c.get("semantic_rank", "–")), "CAMIS": g.camis, "restaurant": g.dba,
                     "date": g.inspection_date_key, "type": g.inspection_type,
                     "source": "exact recovery" if c.get("provenance") == "exact_recovery" else "initial"})
    st.dataframe(rows, hide_index=True, width="stretch")


def render_ledger(panel):
    with panel.container():
        ledger = load_ledger(LESSONS_PATH)
        st.caption("Live local ledger — separate from a replay's historical run counters.")
        st.markdown(f"**{ledger.get('runs', 0)}** runs · **{ledger.get('runs_with_rejections', 0)}** with rejections")
        st.markdown(f"**{ledger.get('retries', 0)}** retries · **{ledger.get('retries_improved', 0)}** improved retries")
        for code, counts in ledger.get("reasons", {}).items():
            st.caption(f"{code}: seen {counts.get('seen', 0)} · fixed by retry {counts.get('fixed_by_retry', 0)}")


def render_result(r: RunResult, recorded_label=None):
    q = r.query
    ev_ids = {g.group_id for g in r.evidence}
    if recorded_label:
        st.info(f"Recorded run replayed offline: `{recorded_label}` — no retrieval or inference now.")

    st.caption("Evidence trace — recorded code actions, retrieval, and validation.")
    st.markdown(f"<span class='step'>STEP 1 · Lock scope</span>", unsafe_allow_html=True)
    st.markdown(scope_text(q))

    st.markdown(f"<span class='step'>STEP 2 · Two independent Elastic rankings</span> "
                f"(rank never authorizes evidence)", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"Lexical · BM25 on title/body · {r.timings.get('lexical', 0):.2f}s")
        branch_table(r.lexical, q)
    with c2:
        st.caption(f"Semantic · semantic_text with Mistral mistral-embed · {r.timings.get('semantic', 0):.2f}s")
        branch_table(r.semantic, q)

    initial = [c for c in r.candidates if c.get("provenance") != "exact_recovery"]
    accepted = [c for c in initial if c["accepted"]]
    reasons = Counter(c["reason"] for c in initial if not c["accepted"])
    st.subheader("🛡️ Evidence gate")
    st.caption("STEP 3 · Deterministic code, before any model call")
    st.markdown(f"Fused **{len(initial)}** groups with weighted RRF (k=60). Admitted **{len(accepted)}**"
                + (", rejected " + ", ".join(f"**{v}** {k}" for k, v in reasons.items()) if reasons else "")
                + ". Rejected groups never reach the model.")
    for c in initial:
        if not c["accepted"] and (c.get("lexical_rank") == 1 or c.get("semantic_rank") == 1):
            b = "Lexical" if c.get("lexical_rank") == 1 else "Semantic"
            st.warning(f"{b} #1 was rejected ({c['reason']}): ranking alone surfaced the wrong scope first.")
    st.caption("⭐ sent to Mistral · ✅ admissible but over cap · ❌ rejected by code")
    gate_table(r.candidates, q, ev_ids)

    if r.counters.get("recovery_rounds"):
        st.markdown("<span class='step'>STEP 4 · Bounded exact recovery (one round, keyword CAMIS + date)</span>",
                    unsafe_allow_html=True)
        total = getattr(r, "recovery_total", None)
        if total is None:
            st.error("Exact lookup FAILED — absence is unproven.")
        elif total == 0:
            st.warning("Exact lookup succeeded: **0** matching groups in this indexed snapshot. "
                       "This is not proof that NYC has no inspection on that date.")
        else:
            st.success(f"Exact lookup returned {total} in-scope group(s).")

    st.markdown("<span class='step'>STEP 5 · Evidence bundle → Mistral → mechanical validation</span>",
                unsafe_allow_html=True)
    attempts = getattr(r, "attempts", None) or []
    proposed = len((getattr(r, "model_response", None) or {}).get("claims", []))
    removed = [g for g in r.gaps if "removed claim" in g]
    if not r.evidence:
        st.markdown("No admissible evidence → **no Mistral call**. The gap is reported instead of a guess.")
    else:
        model = (getattr(r, "integration", {}) or {}).get("mistral_chat_model", "")
        calls = r.counters.get("mistral_chat", 0)
        st.markdown(f"Bundled **{len(r.evidence)}** admitted group(s) · Mistral `{model}` · **{calls}** chat call(s). "
                    + (f"Mistral proposed **{proposed}** claim(s); **{len(r.claims)}** passed checks"
                       f"{f', **{len(removed)}** removed' if removed else ''}." if r.counters.get("mistral_chat") else ""))
        for g in removed:
            st.error(g)

    if len(attempts) > 1:
        st.markdown("**Self-correction: one retry with the validator's feedback**")
        st.caption("The second answer is checked by the same code. No further retry is allowed.")
        retry_events = getattr(r, "frontend_retry_events", None) or []
        for event in retry_events:
            for removed_claim in event.get("removed", []):
                st.error(removed_claim)
        if not retry_events and not removed:
            st.caption("This recording retains rejection codes, but not the removed claim text.")
    if attempts:
        for attempt in attempts:
            number = attempt.get("attempt", "?")
            if attempt.get("error"):
                st.error(f"Attempt {number} → {attempt['error']}")
                continue
            st.markdown(f"**Attempt {number}** → proposed **{attempt.get('proposed', 0)}** · "
                        f"retained **{attempt.get('retained', 0)}**")
            for reason in attempt.get("removed_reasons", []):
                st.error(f"Attempt {number} → removed: {reason}")
        if len(attempts) == 1 and not attempts[0].get("error") and not attempts[0].get("removed_reasons"):
            st.success("First answer passed every check → no retry needed")
    elif r.counters.get("mistral_chat"):
        st.caption("This recorded run does not include an attempts timeline.")
    lessons = getattr(r, "lessons_applied", None) or []
    if lessons:
        st.markdown("**Code-written lessons applied to this run**")
        for lesson in lessons:
            st.markdown(f"- {lesson}")

    # ---- answer card
    with st.container(border=True):
        st.divider()
        colour = {"partial": "#b06000", "supported": "#137333", "insufficient": "#a50e0e", "conflicting": "#a50e0e"}.get(r.status, "#555")
        st.markdown(f"### Answer &nbsp; <span class='badge' style='background:{colour}'>{r.status.upper()}</span>",
                    unsafe_allow_html=True)
        if not r.claims:
            if removed:
                st.markdown("Mistral answered, but no claim passed validation, so nothing is stated as a finding.")
            elif r.evidence and not r.counters.get("mistral_chat"):
                st.markdown("Source-only run: admitted evidence shown without a generated answer.")
            else:
                st.markdown("**No finding can be stated for this scope from the indexed snapshot.**")
        evidence_by_id = {g.group_id: g for g in r.evidence}
        for i, claim in enumerate(r.claims, 1):
            st.markdown(f"<div class='claim'>{i}. {html.escape(claim.text)}</div>", unsafe_allow_html=True)
            for ex in claim.excerpts[:1]:
                if " ".join(ex.quote.split()) != " ".join(claim.text.split()):
                    st.markdown(f"<div class='quote'>“{html.escape(' '.join(ex.quote.split()))}”</div>", unsafe_allow_html=True)
            for tag in evidence_tag(claim, evidence_by_id):
                parts = tag.split(" · ")
                pills = []
                for p in parts:
                    cls = "tag row" if p.startswith("row ") else ("tag code" if p[:3].strip().isalnum() and
                                                                 any(k in p for k in ("Critical", "Not Critical")) else "tag")
                    pills.append(f"<span class='{cls}'>{html.escape(p)}</span>")
                st.markdown("🔗 " + "".join(pills), unsafe_allow_html=True)
            st.markdown("<span class='check'>✔ citation in bundle &nbsp; ✔ verbatim quote &nbsp; ✔ CAMIS/date/type in scope "
                        "&nbsp; ✔ raw row retained</span>", unsafe_allow_html=True)

    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Elastic logical requests", r.counters["elastic_logical"])
    m2.metric("Recovery rounds (max 1)", r.counters["recovery_rounds"])
    m3.metric("Mistral chat calls (max 2)", r.counters["mistral_chat"])
    m4.metric("Total seconds", r.timings.get("total"))
    with st.expander("Disclosed gaps & limits", expanded=True):
        for g in r.gaps:
            if "removed claim" not in g:
                st.markdown(f"- `{g}`")
    with st.expander("Source evidence sent to Mistral"):
        for g in r.evidence:
            st.markdown(f"**{g.group_id}** · coverage `{g.coverage}` · raw rows `{', '.join(g.raw_row_ids)}`")
            st.code(g.body, language=None)
    with st.expander("Raw run JSON"):
        st.json(json.loads(r.model_dump_json()), expanded=False)


# ------------------------------------------------------------------ page

snap = snapshot()
groups, by_camis = catalog()
s = get_settings()

st.title("🔒 LiteralLock NYC")
st.markdown("**Same name. Different restaurant. Show the evidence.** — NYC restaurant inspection Q&A where "
            "identity and inspection date are enforced by code, and every claim carries its evidence.")

with st.sidebar:
    st.header("Data & sponsors")
    st.markdown(f"""
- **NYC Open Data** DOHMH inspections `43nn-pn8j`
- Snapshot `{snap.get('snapshot_id')}`
- {snap.get('unique_raw_row_count')} rows → {snap.get('unique_camis_count')} restaurants → {snap.get('inspection_group_count')} inspection groups
- **Elastic** index `{s.elasticsearch_index}` (BM25 + `semantic_text`)
- **Mistral** embeddings `{s.mistral_embed_model}` via Elastic inference `{s.inference_id}`
- **Mistral** chat `{s.mistral_chat_model}` · 1 normal call + at most 1 validator-triggered retry
""")
    st.caption("Bounded sample, not citywide. Grades are recorded fields, not current status.")
    with st.expander("Self-correction", expanded=True):
        ledger_panel = st.empty()
        render_ledger(ledger_panel)
        st.markdown("Bounded: **one retry max**, only after validator removals. Rules are code-written; "
                    "their effect is not measured. **Not an autonomous self-improving loop.**")
        st.caption("Exact evidence recovery is separate: one round, with at most two extra logical Elastic requests.")
    mode = st.radio("Mode", ["Live", "Replay recorded run"], horizontal=True)

if mode == "Replay recorded run":
    runs = sorted([p for p in DATA_DIR.glob("*_run.json")] + list((DATA_DIR / "live_runs").glob("*.json")))
    default = next((i for i, p in enumerate(runs) if p.name == "subway_exact_run.json"), 0)
    if not runs:
        st.warning("No recorded runs available. Use the CLI fallback when live services are unavailable.")
        st.stop()
    choice = st.selectbox("Recorded run", runs, index=default, format_func=lambda p: str(p.relative_to(DATA_DIR)))
    if choice:
        r = RunResult.model_validate_json(Path(choice).read_text())
        st.markdown(f"**Question:** {r.query.question}")
        render_result(r, recorded_label=str(choice.relative_to(DATA_DIR)))
    st.stop()

# ---- live inputs
question = st.text_input("Question", value=st.session_state.get("question", "What violations were recorded at Subway?"), key="question_input")
resolution = resolve(question, groups)
matches = resolution["candidates"]
if resolution["status"] in ("resolved_by_attributes", "unique_name"):
    st.success("Identity resolved from your words (code, not the model)")
    for evidence in resolution["evidence"]:
        st.markdown(f"- {evidence}")
    st.caption("Resolution is limited to this snapshot. You can change the proposed restaurant below.")
elif resolution["status"] == "ambiguous":
    st.warning("Name is ambiguous — identity NOT resolved")
    for evidence in resolution["evidence"]:
        st.markdown(f"- {evidence}")
    st.dataframe([{"CAMIS": c, "restaurant": g["dba"], "address": g.get("address", "")}
                  for c, g in matches.items()], hide_index=True, width="stretch")

labels = {c: f"{c} — {gs[0]['dba']} — {gs[0]['address']}" for c, gs in sorted(by_camis.items(), key=lambda kv: (kv[1][0]['dba'], kv[0]))}
options = ["(none — conceptual question)"] + [c for c in matches] + [c for c in labels if c not in matches]
if st.session_state.get("resolution_question") != question:
    st.session_state["restaurant_selector"] = resolution["camis"] or options[0]
    st.session_state["resolution_question"] = question
if st.session_state.get("restaurant_selector") not in options:
    st.session_state["restaurant_selector"] = resolution["camis"] or options[0]
col_a, col_b, col_c = st.columns([3, 2, 2])
with col_a:
    camis = st.selectbox("Restaurant (native CAMIS)", options,
                         key="restaurant_selector",
                         format_func=lambda c: c if c.startswith("(") else labels.get(c, c))
camis = None if camis.startswith("(") else camis
date = itype = borough = None
with col_b:
    if camis:
        dates = sorted({g["inspection_date_key"] for g in by_camis[camis]}, reverse=True)
        date_choice = st.selectbox("Inspection date", dates + ["other date…"],
                                   help="Default is the latest date in this snapshot, not the latest city inspection.")
        if date_choice == "other date…":
            date = st.text_input("Date (YYYY-MM-DD)", value="2025-06-15")
        else:
            date = date_choice
    else:
        boros = sorted({g["boro"] for g in groups if g.get("boro")})
        b = st.selectbox("Borough filter (both branches)", ["(any)"] + boros)
        borough = None if b == "(any)" else b
with col_c:
    if camis and date:
        types = sorted({g["inspection_type"] for g in by_camis[camis] if g["inspection_date_key"] == date})
        t = st.selectbox("Inspection type", ["(any)"] + types)
        itype = None if t == "(any)" else t

go = st.button("Run evidence pipeline", type="primary")

if go:
    if resolution["status"] == "ambiguous" and camis is None:
        st.error("Select a restaurant CAMIS to resolve the ambiguous name before running.")
        st.stop()
    from demo import run
    from retrieval import lexical_search, semantic_search
    from scope import make_query
    st.session_state["question"] = question
    try:
        query = make_query(question, camis, date, itype, borough)
    except ValueError as exc:
        st.error(f"Scope error: {exc}")
        st.stop()
    try:
        s, es, mistral, warm = clients()
    except Exception as exc:
        st.error(f"Live services unavailable ({type(exc).__name__}). Switch the sidebar to Replay.")
        st.stop()

    with st.status("Running evidence pipeline…", expanded=True) as status:
        st.write(f"🔒 Scope locked — {scope_text(query)}")
        retry_events = []

        def on_step(event, **d):
            if event == "branch":
                st.write(f"🔎 Elastic {d['name']} branch: {len(d['hits'])} groups in {d['seconds']:.2f}s")
            elif event == "gate":
                acc = sum(1 for c in d["candidates"] if c["accepted"])
                st.write(f"🛡️ Evidence gate: admitted {acc}, rejected {len(d['candidates']) - acc}")
            elif event == "recovery":
                if d["total"] is None:
                    st.error("Exact recovery failed — absence is unproven")
                else:
                    st.write(f"🎯 Exact recovery lookup: {d['total']} group(s) in scope")
            elif event == "retry":
                retry_events.append({"reasons": list(d.get("reasons", [])), "removed": list(d.get("removed", []))})
                st.write("🔁 Self-correction: one retry with the validator's feedback")
                st.caption("Validator reasons: " + ", ".join(d.get("reasons", [])))
                for removed in d.get("removed", []):
                    st.error(removed)
            elif event == "bundle":
                if d["will_answer"]:
                    st.write(f"🧠 Sending {len(d['evidence'])} group(s) to Mistral {s.mistral_chat_model}…")
                else:
                    st.write("⛔ No admissible evidence — no Mistral call")
            elif event == "done":
                st.write(f"✅ Validated {len(d['result'].claims)} claim(s) in {d['result'].timings.get('total')}s")

        try:
            result = run(query, partial(lexical_search, es=es, index=s.elasticsearch_index),
                         partial(semantic_search, es=es, index=s.elasticsearch_index), es, s.elasticsearch_index,
                         mistral, s.mistral_chat_model, snap or {"coverage_notes": "Manifest unavailable"},
                         on_step=on_step, lessons_path=LESSONS_PATH)
        except Exception as exc:
            status.update(label="Pipeline failed", state="error")
            st.error(f"Live run failed ({type(exc).__name__}). Switch the sidebar to Replay.")
            st.stop()
        status.update(label=f"Done · {result.status.upper()} · {result.timings.get('total')}s", state="complete", expanded=False)
    result.frontend_retry_events = retry_events
    out = DATA_DIR / "live_runs" / f"app-{time.strftime('%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(result.model_dump_json(indent=2))
    st.session_state["result"] = result.model_dump_json()
    st.session_state["result_path"] = str(out.relative_to(DATA_DIR))
    render_ledger(ledger_panel)

if "result" in st.session_state:
    r = RunResult.model_validate_json(st.session_state["result"])
    st.caption(f"Last completed run saved as data/{st.session_state['result_path']} · changing inputs affects the next run only.")
    render_result(r)
