"""Compact three-minute presentation over recorded LiteralLock runs (no network calls).

  python present.py                 # full story, Enter between scenes
  python present.py --no-pause      # full story, no pauses
  python present.py intro           # dataset / index / sponsor integration only
  python present.py data/subway_exact_run.json   # one recorded run, compact

Full audit output for any run: python demo.py --replay <run.json>
"""
import json
import sys
import textwrap
from pathlib import Path

from config import DATASET_ID, SNAPSHOT_PATH, get_settings

STORY = [
    ("Same name, different restaurant — and the wrong date ranks first", "data/subway_exact_run.json"),
    ("Same restaurant, same day, different inspection type", "data/codex_reviewed_distractor_run.json"),
    ("Exact lookup for a scope that is not in the snapshot", "data/codex_reviewed_absent_run.json"),
    ("No restaurant selected: conceptual question, semantic-heavy profile", "data/codex_reviewed_conceptual_run.json"),
]

GREEN, RED, DIM, BOLD, CYAN, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[36m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = DIM = BOLD = CYAN = RESET = ""


def rule(title=""):
    print(f"\n{BOLD}{CYAN}== {title} {'=' * max(0, 74 - len(title))}{RESET}")


def short(text, n=110):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def intro():
    s = get_settings()
    m = json.loads(SNAPSHOT_PATH.read_text())
    o = m.get("ingestion_outcome") or {}
    rule("LiteralLock NYC — data and sponsor integration")
    print(f"NYC Open Data DOHMH restaurant inspections, dataset {DATASET_ID}")
    print(f"Snapshot {m['snapshot_id']}  retrieved {m['retrieved_at']}  raw sha256 {m['raw_rows_sha256'][:12]}…")
    print(f"  {m['raw_row_count']} raw rows fetched -> {m['unique_raw_row_count']} unique violation rows "
          f"-> {m['unique_camis_count']} restaurants (CAMIS) -> {m['inspection_group_count']} inspection groups")
    print(f"  Group key: (CAMIS, inspection date, inspection type). Scores/grades kept as observed values, never summed.")
    print(f"  Coverage: {', '.join(f'{k}={v}' for k, v in m['coverage_counts'].items())}")
    print(f"Elastic index {o.get('index', s.elasticsearch_index)}: {o.get('indexed')}/{o.get('attempted')} groups indexed, "
          f"{o.get('failed')} failures")
    print(f"  camis / inspection_date_key / inspection_type / boro: keyword (exact identity + scope)")
    print(f"  title, body: text (BM25 lexical branch)")
    print(f"  body_semantic: semantic_text -> inference endpoint '{s.inference_id}' "
          f"(Mistral {s.mistral_embed_model}, 1024 dims)")
    print(f"Mistral chat: {s.mistral_chat_model} — one structured assessment+answer call per run")
    print(f"{DIM}Limits: bounded sample, not citywide; grades are recorded fields, not current status.{RESET}")


def show_run(path):
    r = json.loads(Path(path).read_text())
    q = r["query"]
    ev = r.get("evidence") or []
    subject = ""
    if q.get("camis"):
        g = next((c["group"] for c in r["candidates"] if c["group"]["camis"] == q["camis"]), None)
        if g:
            subject = f"  {g.get('dba', '')}, {g.get('address', '')}"
    print(f"{BOLD}Q:{RESET} {q['question']}")
    if q.get("camis"):
        print(f"{BOLD}Scope:{RESET} CAMIS {q['camis']} | date {q['inspection_date_key']} | "
              f"type {q.get('inspection_type') or 'any'}{subject}")
    else:
        print(f"{BOLD}Scope:{RESET} no restaurant selected" + (f" | borough {q['borough']}" if q.get("borough") else ""))
    print(f"{BOLD}Routing:{RESET} {q['routing_profile']} -> lexical {q['lexical_weight']} / semantic {q['semantic_weight']} "
          f"(weighted RRF, k=60)")

    in_bundle = {g["group_id"] for g in ev}
    names = {c["group"]["group_id"]: c["group"] for c in r["candidates"]}
    print(f"\n{BOLD}  Lex Sem  Decision             CAMIS     Restaurant            Date        Type{RESET}")
    for c in r["candidates"]:
        g = c["group"]
        ok = c["accepted"]
        tag = "★" if g["group_id"] in in_bundle else ("·" if ok else "✘")
        reason = c["reason"] if g["group_id"] in in_bundle or not ok else c["reason"][:10] + " (cap)"
        mark = f"{GREEN}{tag} {reason:<18}{RESET}" if ok else f"{RED}{tag} {reason:<18}{RESET}"
        prov = "" if c.get("provenance") == "initial" else f" {DIM}[{c.get('provenance')}]{RESET}"
        print(f"  {str(c.get('lexical_rank', '-')):>3} {str(c.get('semantic_rank', '-')):>3}  {mark}  "
              f"{g['camis']:<9} {short(g.get('dba', ''), 20):<20}  {g.get('inspection_date_key')}  "
              f"{short(g.get('inspection_type') or '', 34)}{prov}")
    if not r["candidates"]:
        print(f"  {DIM}(no candidates){RESET}")

    print(f"\n{BOLD}Findings ({r['status'].upper()}){RESET}")
    if not r["claims"]:
        print(f"  {DIM}No findings stated — no admissible evidence for this scope.{RESET}")
    for cl in r["claims"]:
        src = names.get(cl["source_ids"][0], {})
        who = f"CAMIS {cl['camis']} {src.get('dba', '')} | {cl['inspection_date_key']}"
        print(f"  • {CYAN}{who}{RESET}")
        print(textwrap.fill(short(cl["text"], 180), 96, initial_indent="    ", subsequent_indent="    "))
        for ex in cl["excerpts"][:1]:
            if " ".join(ex["quote"].split()) != " ".join(cl["text"].split()):
                print(f"    {DIM}quote: “{short(ex['quote'], 88)}”{RESET}")
            print(f"    {DIM}cite {ex['source_id']}{RESET}")
    if ev:
        print(f"{DIM}  ★ = sent to Mistral ({len(ev)} group(s), max 4); · = admissible but over cap; ✘ = rejected by code{RESET}")

    print(f"\n{BOLD}Disclosed gaps{RESET}")
    for gap in r["gaps"]:
        print(f"  - {short(gap, 100)}")
    c, t = r["counters"], r["timings"]
    print(f"\n{DIM}Elastic logical requests {c['elastic_logical']} (recovery {c['recovery_logical']}, rounds {c['recovery_rounds']}) | "
          f"Mistral chat calls {c['mistral_chat']} | total {t.get('total')} s | recorded run, replayed offline{RESET}")


def main(argv):
    pause = "--no-pause" not in argv
    args = [a for a in argv if not a.startswith("--")]
    if args and args[0] == "intro":
        intro()
        return
    if args:
        for path in args:
            rule(Path(path).stem)
            show_run(path)
        return
    intro()
    for i, (title, path) in enumerate(STORY, start=1):
        if pause:
            input(f"\n{DIM}[Enter] scene {i}{RESET}")
        rule(f"{i}. {title}")
        show_run(path)


if __name__ == "__main__":
    main(sys.argv[1:])
