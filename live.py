"""Interactive LiteralLock demo: type a question, watch the evidence pipeline, get a cited answer.

  python live.py                       # interactive prompts (live Elastic + Mistral)
  python live.py "question" --camis 41367337 --date 2026-10-05 [--type T] [--borough B] [--save f.json]
  python live.py --replay data/subway_exact_run.json   # same visuals from a recorded run, offline

The trace shows what the pipeline actually did (scope lock, both rankings, code gate,
recovery, validation). It is not hidden model reasoning.
"""
import argparse
import json
import re
import sys
import textwrap
import time
from collections import Counter, defaultdict
from pathlib import Path

from config import GROUPS_PATH, LESSONS_PATH, SNAPSHOT_PATH, get_settings
from contracts import InspectionGroup, RunResult

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "c": "\033[36m", "m": "\033[35m",
     "b": "\033[1m", "d": "\033[2m", "0": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}
W = 100
PACE = 0.25  # seconds between rendered stages, for narration


def col(text, *codes):
    return "".join(C[c] for c in codes) + str(text) + C["0"]


def short(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def step(n, title, note=""):
    time.sleep(PACE)
    print()
    print(col(f"┌─ STEP {n} · {title} ", "b", "c") + col("─" * max(0, W - len(title) - 12), "c"))
    if note:
        for line in textwrap.wrap(note, W - 4):
            print(col("│ ", "c") + line)


def line(text=""):
    print(col("│ ", "c") + text)


# ------------------------------------------------------------------ catalog (offline, no Elastic call)

def load_catalog():
    if not GROUPS_PATH.exists():
        return [], {}
    groups = json.loads(GROUPS_PATH.read_text())
    by_camis = defaultdict(list)
    for g in groups:
        by_camis[g["camis"]].append(g)
    return groups, by_camis


from resolve import auto_scope, name_matches, resolve  # noqa: E402  (re-exported for app.py)


# ------------------------------------------------------------------ renderers (shared by live + replay)

def cell_match(value, wanted):
    if wanted is None:
        return str(value)
    return col(value, "g") if value == wanted else col(value, "r")


def render_auto(question, scope):
    title = {"entity": "Understand the question → identity & date from your words (code, no model)",
             "conceptual": "Understand the question → no restaurant named (code, no model)",
             "ambiguous": "Understand the question → identity is ambiguous (code, no model)"}[scope["mode"]]
    step(0, title)
    for e in scope["steps"]:
        line(col("· " + e, "r" if scope["mode"] == "ambiguous" and e.startswith("identity NOT") else "g" if e.startswith("→") else "0"))
    if scope["mode"] == "ambiguous":
        line(col(f"→ Not guessing. Answering each of the {len(scope['resolution']['candidates'])} identities "
                 "separately, each locked to its own CAMIS:", "y", "b"))
        for c, g in scope["resolution"]["candidates"].items():
            line(col(f"   CAMIS {c}  {g['dba']}  {g['address']}", "y"))


def render_scope(q, by_camis):
    if q.camis:
        why = (f"Explicit CAMIS → exact-entity profile. Identity = native CAMIS {q.camis}, "
               f"scope = {q.inspection_date_key}" + (f" / {q.inspection_type}" if q.inspection_type else " / any type")
               + ". Names and addresses are labels, not locks.")
    else:
        why = "No CAMIS → conceptual profile. Every example keeps its own CAMIS and date." + (
            f" Borough filter '{q.borough}' applied to both branches." if q.borough else "")
    step(1, "Lock scope", why)
    if q.camis and by_camis.get(q.camis):
        g = by_camis[q.camis][0]
        line(f"Subject: {col(g['dba'], 'b')}  {g['address']}")
    line(f"Routing: {col(q.routing_profile, 'b')}  lexical {col(q.lexical_weight, 'y')} / semantic "
         f"{col(q.semantic_weight, 'y')}  (weighted reciprocal-rank fusion, k=60)")


def render_branch(n, name, hits, seconds, q):
    label = {"lexical": "Elastic lexical branch (BM25 on title/body)",
             "semantic": "Elastic semantic branch (semantic_text · Mistral mistral-embed)"}[name]
    step(n, label, f"{len(hits)} groups in {seconds:.2f}s. Ranked independently; rank never authorizes evidence.")
    for h in hits:
        g = h.group
        line(f"{h.rank:>2}. {cell_match(g.camis, q.camis):<8}  {short(g.dba, 22):<22}  "
             f"{cell_match(g.inspection_date_key, q.inspection_date_key):<10}  {short(g.inspection_type or '', 36):<36} "
             f"{col(f'score {h.score:.3f}', 'd')}")


def as_group(g):
    return g if isinstance(g, InspectionGroup) else InspectionGroup.model_validate(g)


def render_gate(n, candidates, q):
    accepted = [c for c in candidates if c["accepted"]]
    reasons = Counter(c["reason"] for c in candidates if not c["accepted"])
    note = (f"Fused {len(candidates)} unique groups. Code admitted {len(accepted)}"
            + (f", rejected {sum(reasons.values())} (" + ", ".join(f"{v} {k}" for k, v in reasons.items()) + ")" if reasons else "")
            + (f". Top 4 admitted by fused rank go to the bundle" if len(accepted) > 4 else "")
            + ". The model never sees rejected groups.")
    step(n, "Evidence gate (deterministic code, before any LLM)", note)
    for c in candidates:
        g = as_group(c["group"])
        lr, sr = c.get("lexical_rank", "-"), c.get("semantic_rank", "-")
        mark = col("✔ ADMIT ", "g", "b") if c["accepted"] else col("✘ REJECT", "r", "b")
        why = c["reason"]
        if not c["accepted"] and q.camis:
            if why == "ENTITY_MISMATCH":
                why += f" (CAMIS {g.camis} ≠ {q.camis})"
            elif why == "DATE_MISMATCH":
                why += f" ({g.inspection_date_key} ≠ {q.inspection_date_key})"
            elif why == "TYPE_MISMATCH":
                why += f" ({short(g.inspection_type, 24)})"
        prov = col(" [exact recovery]", "m") if c.get("provenance") == "exact_recovery" else ""
        line(f"{mark} L{str(lr):>2} S{str(sr):>2}  {g.camis:<8}  {short(g.dba, 20):<20} {g.inspection_date_key}  "
             f"{col(why, 'g' if c['accepted'] else 'r')}{prov}")
    top = [c for c in candidates if not c["accepted"] and (c.get("lexical_rank") == 1 or c.get("semantic_rank") == 1)]
    for c in top:
        b = "lexical" if c.get("lexical_rank") == 1 else "semantic"
        line(col(f"⚠ The {b} branch's #1 result was rejected ({c['reason']}): ranking alone surfaced the wrong scope first.", "y", "b"))


def render_recovery(n, rows, gaps, total):
    step(n, "Bounded exact recovery (1 round, keyword CAMIS + date filter)",
         "Scoped evidence was missing from both branches, so code issued one exact lookup. No relaxing to similar names.")
    if total is None:
        line(col("Lookup FAILED — absence is unproven.", "r", "b"))
    elif total == 0:
        line(col("Lookup succeeded: 0 matching groups in this indexed snapshot.", "y", "b"))
        line(col("This is NOT proof that NYC has no inspection on that date.", "d"))
    else:
        line(col(f"Lookup returned {total} group(s) in scope.", "g", "b"))
    for g in gaps:
        line(col(g, "d"))


def render_bundle(n, evidence, will_answer, settings_model):
    if not evidence:
        step(n, "Answer decision", "No admissible evidence → no Mistral call. The system reports the gap instead of guessing.")
        return
    chars = sum(len(g.body) for g in evidence)
    note = (f"{len(evidence)} admitted group(s), {chars} characters, max 4 groups. "
            + (f"Sending to Mistral {settings_model} — one structured assess-and-answer call."
               if will_answer else "Mistral disabled for this run (source-only)."))
    step(n, "Evidence bundle → Mistral", note)
    for g in evidence:
        line(f"• {g.group_id}  ({g.coverage}, {len(g.raw_row_ids)} raw rows)")


def render_retry(n, reasons, removed):
    step(n, "Self-correction: one retry with the validator's feedback",
         f"Code removed {len(removed)} claim(s). Their rejection reasons and fixed rules go back to Mistral once; "
         "the retry must pass the same checks. No further retries.")
    for g in removed:
        line(col("✘ " + short(g, W - 6), "r"))
    line(col("… waiting for Mistral retry …", "m"))
    sys.stdout.flush()


def render_lessons(r):
    lessons = getattr(r, "lessons_applied", None) or []
    if lessons:
        line(col(f"Lessons from earlier runs added to the prompt ({len(lessons)} fixed rules):", "m"))
        for l in lessons:
            line(col("  • " + short(l, W - 8), "m"))


def evidence_tag(claim, evidence_by_id):
    tags = []
    for ex in claim.excerpts:
        g = evidence_by_id.get(ex.source_id)
        if g is None:
            continue
        v = next((v for v in getattr(g, "violations", []) or []
                  if v.get("violation_description") and (ex.quote in v["violation_description"]
                                                         or v["violation_description"] in ex.quote)), None)
        bits = [f"CAMIS {g.camis}", g.dba, getattr(g, "address", ""), g.inspection_date_key, short(g.inspection_type or "", 38)]
        if v:
            bits.append(f"{v['violation_code']} {v.get('critical_flag') or ''}".strip())
            bits.append(f"row {v['row_id']}")
        tags.append(" · ".join(b for b in bits if b))
    return tags


def render_answer(n, r):
    proposed = len((getattr(r, "model_response", None) or {}).get("claims", []))
    removed = [g for g in r.gaps if "removed claim" in g]
    attempts = getattr(r, "attempts", None) or []
    if proposed or attempts:
        step(n, "Mechanical validation of Mistral output",
             "Checked: cited group exists in the bundle, quote appears verbatim, claim CAMIS/date/type = locked "
             "scope, raw-row provenance retained, no completeness or conflicting-score claims.")
        render_lessons(r)
        for a in attempts:
            if "error" in a:
                line(col(f"Attempt {a['attempt']}: failed ({a['error']})", "r"))
                continue
            ok = not a["removed_reasons"]
            line(col(f"Attempt {a['attempt']}: proposed {a['proposed']}, passed {a['retained']}"
                     + (f", removed {len(a['removed_reasons'])} ({', '.join(dict.fromkeys(a['removed_reasons']))})" if not ok else " — all passed"),
                     "g" if ok else "y"))
        if not attempts:
            line(f"Mistral proposed {proposed} claim(s); {len(r.claims)} passed; {len(removed)} removed.")
        if len(attempts) == 1 and not attempts[0].get("removed_reasons"):
            line(col("First answer passed every check → no retry needed.", "d"))
        for g in removed:
            line(col("✘ " + short(g, W - 6), "r"))
        ledger = getattr(r, "ledger", None)
        if ledger:
            line(col(f"Lessons ledger: {ledger['runs']} runs · {ledger['runs_with_rejections']} with rejections · "
                     f"{ledger['retries']} retries ({ledger['retries_improved']} improved)", "d"))
    ev = {g.group_id: g for g in r.evidence}

    print()
    status_col = {"partial": "y", "supported": "g", "insufficient": "r", "conflicting": "r"}.get(r.status, "y")
    print(col("╔" + "═" * (W - 2) + "╗", "b"))
    print(col("║ ANSWER ", "b") + col(f"[{r.status.upper()}]", status_col, "b"))
    print(col("╚" + "═" * (W - 2) + "╝", "b"))
    if not r.claims and removed:
        print(textwrap.fill("Mistral answered, but no claim passed mechanical validation, so nothing is stated "
                            "as a finding (see step above). Source evidence remains inspectable.", W))
    elif not r.claims and r.evidence and r.counters.get("mistral_chat", 0) == 0:
        print(textwrap.fill("Source-only mode: Mistral answering was disabled for this run. "
                            "The admitted evidence groups listed above are shown without a generated answer.", W))
    elif not r.claims:
        print(textwrap.fill("No finding can be stated for this scope from the indexed snapshot. "
                            "The gaps below say exactly why.", W))
    for i, claim in enumerate(r.claims, 1):
        print()
        print(textwrap.fill(claim.text, W, initial_indent=f" {i}. ", subsequent_indent="    "))
        for tag in evidence_tag(claim, ev):
            print(col(textwrap.fill(tag, W, initial_indent="    ▸ evidence: ", subsequent_indent="                "), "c"))
        for ex in claim.excerpts[:1]:
            if " ".join(ex.quote.split()) != " ".join(claim.text.split()):
                print(textwrap.fill(f"quote: “{' '.join(ex.quote.split())}”", W,
                                    initial_indent="      ", subsequent_indent="              "))
        print(col("      ✔ citation in bundle  ✔ verbatim quote  ✔ CAMIS/date/type in scope  ✔ raw row retained", "g"))

    print()
    print(col("Disclosed gaps & limits", "b"))
    for g in r.gaps:
        if "removed claim" in g:
            continue
        print(col("  - " + short(g, W - 4), "d"))
    c, t = r.counters, r.timings
    print(col(f"\nBudget: Elastic logical requests {c['elastic_logical']} (recovery {c['recovery_logical']}, max 1 round) · "
              f"Mistral chat calls {c['mistral_chat']} (max 1) · total {t.get('total')} s", "d"))


# ------------------------------------------------------------------ live + replay drivers

class LiveRenderer:
    def __init__(self, query, model):
        self.q, self.model, self.n = query, model, 1
        self.t_answer = None

    def __call__(self, event, **d):
        if event == "branch":
            self.n += 1
            render_branch(self.n, d["name"], d["hits"], d["seconds"], self.q)
        elif event == "gate":
            self.n += 1
            render_gate(self.n, d["candidates"], self.q)
        elif event == "recovery":
            self.n += 1
            render_recovery(self.n, d["rows"], d["gaps"], d["total"])
        elif event == "bundle":
            self.n += 1
            render_bundle(self.n, d["evidence"], d["will_answer"], self.model)
            if d["will_answer"]:
                line(col("… waiting for Mistral (structured output) …", "m"))
                sys.stdout.flush()
        elif event == "retry":
            self.n += 1
            render_retry(self.n, d["reasons"], d["removed"])
        elif event == "done":
            self.n += 1
            render_answer(self.n, d["result"])


def replay(path):
    r = RunResult.model_validate_json(Path(path).read_text())
    _, by_camis = load_catalog()
    q = r.query
    print(col(f"RECORDED RUN (offline replay of {path}; no retrieval or inference now)", "m", "b"))
    print(col("Q: ", "b") + q.question)
    render_scope(q, by_camis)
    n = 1
    for name in ("lexical", "semantic"):
        n += 1
        render_branch(n, name, getattr(r, name), r.timings.get(name, 0.0), q)
    initial = [c for c in r.candidates if c.get("provenance") != "exact_recovery"]
    rec = [c for c in r.candidates if c.get("provenance") == "exact_recovery"]
    n += 1
    render_gate(n, initial, q)
    if r.counters.get("recovery_rounds"):
        n += 1
        render_recovery(n, rec, [g for g in r.gaps if g.startswith(("LOOKUP_", "CONTEXT_EXCERPTED: exact"))],
                        getattr(r, "recovery_total", None))
    n += 1
    render_bundle(n, r.evidence, r.counters.get("mistral_chat", 0) > 0,
                  (getattr(r, "integration", {}) or {}).get("mistral_chat_model"))
    attempts = getattr(r, "attempts", None) or []
    if len(attempts) > 1:
        n += 1
        render_retry(n, attempts[0]["removed_reasons"], [f"{c}: removed claim" for c in attempts[0]["removed_reasons"]])
    render_answer(n + 1, r)


def ask(prompt, default=None):
    try:
        v = input(col(prompt, "b")).strip()
    except EOFError:
        return default
    return v or default


def interactive_inputs(groups, by_camis):
    question = ask("Question> ")
    if not question:
        return None
    m = re.search(r"\bCAMIS[:\s#]*(\d{8})\b", question, re.I)
    camis = m.group(1) if m else None
    proposed = None
    if not camis:
        res = resolve(question, groups)
        if res["status"] != "no_name":
            colour = "g" if res["camis"] else "y"
            title = {"unique_name": "Name matches one restaurant",
                     "resolved_by_attributes": "Identity resolved from your words (code, not the model)",
                     "ambiguous": "Name is ambiguous — identity NOT resolved"}[res["status"]]
            print(col(f"  {title}:", colour, "b"))
            for e in res["evidence"]:
                print(col(f"    · {e}", colour))
            for c, g in res["candidates"].items():
                mark = "→" if c == res["camis"] else " "
                print(col(f"   {mark} CAMIS {c}  {g['dba']}  {g['address']}", colour if c == res["camis"] else "d"))
            proposed = res["camis"]
    while not camis:
        hint = f"Enter = confirm {proposed}" if proposed else "Enter = none → conceptual search"
        camis = ask(f"CAMIS ({hint})> ", proposed)
        if camis is None:
            break
        if not re.fullmatch(r"\d{8}", camis):
            print(col("  CAMIS is the 8-digit restaurant ID (e.g. 41456579). Try again, or Enter for none.", "y"))
            camis = None
            continue
    date = itype = borough = None
    if camis:
        if camis not in by_camis:
            print(col(f"  CAMIS {camis} is not in this snapshot; the exact lookup will report that.", "y"))
        dates = sorted({g["inspection_date_key"] for g in by_camis.get(camis, [])}, reverse=True)
        if dates:
            print(col(f"  Dates in this snapshot for {camis}: {', '.join(dates)}", "d"))
        default = dates[0] if dates else None
        while True:
            date = ask(f"Inspection date YYYY-MM-DD (Enter = {default}, latest in this snapshot — not latest city inspection)> ", default)
            if date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                break
            print(col("  Use the format YYYY-MM-DD, e.g. 2026-10-05.", "y"))
        types = sorted({g["inspection_type"] for g in by_camis.get(camis, []) if g["inspection_date_key"] == date})
        if len(types) > 1:
            print(col(f"  {len(types)} inspection types on {date}: " + " | ".join(types), "d"))
        itype = ask("Inspection type (Enter = any)> ")
    else:
        borough = ask("Borough filter (Enter = none)> ")
    return question, camis, date, itype, borough


def main():
    global PACE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?")
    ap.add_argument("--camis")
    ap.add_argument("--date", dest="inspection_date_key")
    ap.add_argument("--type", dest="inspection_type")
    ap.add_argument("--borough")
    ap.add_argument("--source-only", action="store_true")
    ap.add_argument("--save")
    ap.add_argument("--replay")
    ap.add_argument("--fast", action="store_true", help="no pacing between stages")
    ap.add_argument("--auto", action="store_true",
                    help="one-line mode: type only a question; identity/date/borough come from your words")
    a = ap.parse_args()
    if a.fast:
        PACE = 0
    if a.replay:
        replay(a.replay)
        return

    groups, by_camis = load_catalog()
    print(col("LiteralLock NYC · restaurant inspection evidence · Elastic × Mistral", "b", "c"))
    snap = json.loads(SNAPSHOT_PATH.read_text()) if SNAPSHOT_PATH.exists() else {}
    print(col(f"Snapshot {snap.get('snapshot_id')} · {snap.get('unique_camis_count')} restaurants · "
              f"{snap.get('inspection_group_count')} inspection groups · bounded sample, not citywide", "d"))

    from functools import partial
    from config import get_es
    from demo import run
    from retrieval import lexical_search, semantic_search
    from scope import make_query
    s = get_settings()
    try:
        es = get_es(s)
    except Exception as exc:
        raise SystemExit(f"Elastic client unavailable ({type(exc).__name__}). Use: python live.py --replay <run.json>")
    # Warm the HTTPS connection (setup only; not a retrieval request and not counted in run budgets).
    try:
        t0 = time.perf_counter()
        es.info()
        print(col(f"Elastic connected ({time.perf_counter() - t0:.2f}s warm-up) · index {s.elasticsearch_index}", "d"))
    except Exception as exc:
        print(col(f"Elastic warm-up failed ({type(exc).__name__}); live runs may fail. "
                  "Fallback: python live.py --replay data/live_subway_brooklyn_run.json", "r", "b"))
    mistral = None
    if not a.source_only:
        from mistralai.client import Mistral
        mistral = Mistral(api_key=s.mistral_api_key, timeout_ms=30000, retry_config=None)
    snapshot = snap or {"coverage_notes": "Manifest unavailable"}

    def one_run(question, camis, date, itype, borough, save_path):
        try:
            query = make_query(question, camis, date, itype, borough)
        except ValueError as exc:
            print(col(f"Scope error: {exc}", "r", "b"))
            return
        render_scope(query, by_camis)
        try:
            result = run(query, partial(lexical_search, es=es, index=s.elasticsearch_index),
                         partial(semantic_search, es=es, index=s.elasticsearch_index), es, s.elasticsearch_index,
                         mistral, s.mistral_chat_model, snapshot, on_step=LiveRenderer(query, s.mistral_chat_model),
                         lessons_path=LESSONS_PATH)
        except KeyboardInterrupt:
            print(col("\nRun cancelled.", "y", "b"))
            return
        except Exception as exc:
            print(col(f"\nLive run failed ({type(exc).__name__}). Fallback: python live.py --replay data/subway_exact_run.json", "r", "b"))
            return
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(result.model_dump_json(indent=2))
        print(col(f"saved {save_path}", "d"))

    def auto_run(question, save_path):
        scope = auto_scope(question, groups)
        print(col("Q: ", "b") + question)
        render_auto(question, scope)
        if scope["mode"] == "ambiguous":
            # No guessing: answer every same-name identity separately, each with its own locked scope.
            cands = list(scope["resolution"]["candidates"].items())[:3]
            for i, (c, g) in enumerate(cands, 1):
                dates = sorted({x["inspection_date_key"] for x in groups if x["camis"] == c}, reverse=True)
                print()
                print(col(f"━━━ Identity {i} of {len(cands)}: CAMIS {c} · {g['dba']} · {g['address']} "
                          f"· latest snapshot date {dates[0]} ━━━", "m", "b"))
                one_run(question, c, dates[0], None, None, save_path.with_name(save_path.stem + f"-{c}.json"))
            print(col(f"\nAnswered {len(cands)} restaurants separately — evidence never merged across CAMIS. "
                      "Add a street, borough or ZIP to focus on one.", "y", "b"))
            return
        one_run(question, scope["camis"], scope["date"], None, scope["borough"], save_path)

    def save_to():
        return Path(a.save) if a.save else Path("data/live_runs") / f"run-{time.strftime('%H%M%S')}.json"

    if a.auto:
        if a.question:
            auto_run(a.question, save_to())
            return
        print(col("One-line mode: just ask. Enter on an empty question or Ctrl+C to quit.", "d"))
        while True:
            try:
                q = ask("Question> ")
            except (KeyboardInterrupt, EOFError):
                q = None
            if not q:
                print(col("Exiting LiteralLock.", "d"))
                return
            auto_run(q, save_to())
            print(col("\n" + "─" * W + "\nNext question (Enter to quit)", "c"))

    if a.question:
        one_run(a.question, a.camis, a.inspection_date_key, a.inspection_type, a.borough,
                Path(a.save) if a.save else Path("data/live_runs") / f"run-{time.strftime('%H%M%S')}.json")
        return
    print(col("Type a question. Enter on an empty question or Ctrl+C to quit.", "d"))
    while True:
        try:
            inputs = interactive_inputs(groups, by_camis)
        except (KeyboardInterrupt, EOFError):
            print(col("\nExiting LiteralLock.", "d"))
            return
        if inputs is None:
            print(col("Exiting LiteralLock.", "d"))
            return
        one_run(*inputs, Path(a.save) if a.save else Path("data/live_runs") / f"run-{time.strftime('%H%M%S')}.json")
        print(col("\n" + "─" * W + "\nNext question (Enter to quit)", "c"))


if __name__ == "__main__":
    main()
