"""Terminal presentation and injectable pipeline; run --help without services."""
import argparse
import json
import time
from pathlib import Path
from contracts import RunResult, SearchHit
from scope import make_query
from evidence import fuse, select_bundle
from recovery import recover
from answer import assess_and_answer, load_ledger, lessons_from, removed_reasons, update_ledger


def run(query, lexical_search, semantic_search, es, index, mistral_client=None,
        model=None, snapshot=None, deadline_seconds=60, on_step=None, lessons_path=None, self_correct=True):
    """on_step(event, **data) is an optional display hook; it never changes pipeline decisions."""
    emit = on_step or (lambda event, **data: None)
    start = time.monotonic()
    result = RunResult(query=query, snapshot=snapshot or {})
    result.integration = {"elasticsearch_index": index, "mistral_chat_model": model,
                          "mistral_answer_enabled": mistral_client is not None}
    for name, search in [("lexical", lexical_search), ("semantic", semantic_search)]:
        if time.monotonic() - start >= deadline_seconds:
            result.gaps.append("TIME_BUDGET: retrieval skipped")
            continue
        branch_start = time.monotonic()
        result.counters["elastic_logical"] += 1
        try:
            hits = [SearchHit.model_validate(h) for h in search(query, limit=6)]
            setattr(result, name, hits[:6])
        except Exception as exc:
            result.gaps.append(f"{name.upper()}_FAILED: {type(exc).__name__}; retrieval unavailable")
        result.timings[name] = round(time.monotonic() - branch_start, 3)
        emit("branch", name=name, hits=getattr(result, name), seconds=result.timings[name])
    result.candidates = fuse(query, result.lexical, result.semantic)
    emit("gate", candidates=result.candidates)
    if query.camis and not any(r["accepted"] for r in result.candidates):
        if time.monotonic() - start < deadline_seconds:
            rows, gaps, total = recover(es, index, query, result.counters)
            result.candidates.extend(rows)
            result.gaps.extend(gaps)
            result.recovery_total = total
            emit("recovery", rows=rows, gaps=gaps, total=total)
        else:
            result.gaps.append("TIME_BUDGET: recovery skipped; absence unproven")
    result.evidence, gaps = select_bundle(result.candidates)
    result.gaps.extend(gaps)
    emit("bundle", evidence=result.evidence, gaps=gaps, will_answer=bool(result.evidence and mistral_client is not None))
    result.gaps.append("SNAPSHOT_LIMITED: bounded snapshot; coverage beyond retained rows is unknown")
    if result.evidence and mistral_client is not None:
        remaining_ms = int((deadline_seconds - (time.monotonic() - start)) * 1000)
        if remaining_ms <= 0:
            result.gaps.append("TIME_BUDGET: answer skipped; source evidence shown")
        else:
            answer_start = time.monotonic()
            ledger = load_ledger(lessons_path) if lessons_path else None
            result.lessons_applied = lessons_from(ledger) if ledger else []
            result.attempts = []
            first_codes, retry_codes, retry_used = [], None, False
            try:
                answer, claims, gaps, status = assess_and_answer(
                    mistral_client, model, query, result.evidence, result.snapshot,
                    result.counters, timeout_ms=min(30000, remaining_ms), lessons=result.lessons_applied)
                first_codes = removed_reasons(gaps)
                result.attempts.append({"attempt": 1, "proposed": len(answer.claims), "retained": len(claims),
                                        "removed_reasons": first_codes})
                chosen = (answer, claims, gaps, status)
                remaining_ms = int((deadline_seconds - (time.monotonic() - start)) * 1000)
                if first_codes and self_correct and remaining_ms > 0:
                    # Bounded self-correction: one retry with the validator's own rejection reasons.
                    emit("retry", reasons=first_codes, removed=[g for g in gaps if ": removed claim: " in g])
                    try:
                        a2, c2, g2, s2 = assess_and_answer(
                            mistral_client, model, query, result.evidence, result.snapshot, result.counters,
                            timeout_ms=min(30000, remaining_ms), lessons=result.lessons_applied,
                            feedback={"previous": answer.model_dump_json(), "codes": first_codes,
                                      "removed": [g for g in gaps if ": removed claim: " in g]})
                        retry_codes = removed_reasons(g2)
                        result.attempts.append({"attempt": 2, "proposed": len(a2.claims), "retained": len(c2),
                                                "removed_reasons": retry_codes})
                        if len(c2) > len(claims):  # keep the attempt with more code-verified claims
                            chosen, retry_used = (a2, c2, g2, s2), True
                    except Exception as exc:
                        retry_codes = []
                        result.attempts.append({"attempt": 2, "error": type(exc).__name__})
                answer, result.claims, gaps, result.status = chosen
                result.model_response = answer.model_dump()
                result.gaps.extend(gaps)
                if len(result.attempts) > 1:
                    result.gaps.append(f"SELF_CORRECTION: attempt 1 removed {len(first_codes)} claim(s) "
                                       f"({', '.join(dict.fromkeys(first_codes))}); one retry made; "
                                       f"{'retry kept' if retry_used else 'attempt 1 kept'}")
            except Exception as exc:
                result.gaps.append(f"ANSWER_FAILED: {type(exc).__name__}; source-only fallback, no repair call")
            if ledger is not None and result.attempts:
                result.ledger = update_ledger(lessons_path, first_codes, retry_codes, retry_used)
            result.timings["answer"] = round(time.monotonic() - answer_start, 3)
    elif result.evidence:
        result.gaps.append("SOURCE_ONLY: Mistral answering disabled for this run")
    result.timings["total"] = round(time.monotonic() - start, 3)
    result.gaps = list(dict.fromkeys(result.gaps))
    emit("done", result=result)
    return result


def display(result):
    q = result.query
    print(f"LiteralLock NYC | {result.status.upper()}")
    print(f"CAMIS={q.camis or 'conceptual examples'} date={q.inspection_date_key or 'per source'} type={q.inspection_type or 'per source'}")
    print(f"Profile={q.routing_profile} lexical={q.lexical_weight} semantic={q.semantic_weight}")
    print("INTEGRATION", json.dumps(getattr(result, "integration", {})))
    for name in ["lexical", "semantic"]:
        print(f"\n{name.upper()} RANKING")
        for hit in getattr(result, name):
            print(f"{hit.rank:2} {hit.group_id} score={hit.score} CAMIS={hit.group.camis} date={hit.group.inspection_date_key}")
    print("\nCANDIDATE AUDIT")
    for row in result.candidates:
        print(f"{row['group_id']} L={row.get('lexical_rank', '-')} S={row.get('semantic_rank', '-')} {row['reason']} {row['provenance']}")
    print("\nFINDINGS")
    for claim in result.claims:
        print(f"- {claim.text} [{', '.join(claim.source_ids)}]")
        for excerpt in claim.excerpts:
            print(f"  {excerpt.source_id}: {excerpt.quote}")
    print("\nSOURCE EVIDENCE")
    for group in result.evidence:
        print(f"[{group.group_id}] {group.dba} CAMIS={group.camis} {group.inspection_date_key} {group.inspection_type}")
        print(f"coverage={group.coverage}; raw rows={group.raw_row_ids}")
        print(group.body)
    print("\nGAPS")
    for gap in result.gaps:
        print(f"- {gap}")
    print("\nCOUNTERS", json.dumps(result.counters))
    print("TIMINGS seconds", json.dumps(result.timings))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--camis")
    parser.add_argument("--date", dest="inspection_date_key")
    parser.add_argument("--type", dest="inspection_type")
    parser.add_argument("--borough")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--save", help="Save actual run JSON as a connectivity fallback")
    parser.add_argument("--replay", help="Display saved run without network calls")
    args = parser.parse_args()
    if args.replay:
        display(RunResult.model_validate_json(Path(args.replay).read_text()))
        print("RECORDED PRIOR RUN: replay performed no retrieval or inference")
        return
    if not args.question:
        parser.error("question is required unless using --replay")
    query = make_query(args.question, args.camis, args.inspection_date_key, args.inspection_type, args.borough)
    # Claude's adapter interfaces are finalized during integration.
    from config import get_settings, get_es, LESSONS_PATH
    from retrieval import lexical_search, semantic_search
    from functools import partial
    settings = get_settings()
    es = get_es(settings)
    snapshot_path = Path("data/snapshot.json")
    snapshot = json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {"coverage_notes": "Manifest unavailable"}
    mistral = None
    if not args.source_only:
        from mistralai.client import Mistral
        mistral = Mistral(api_key=settings.mistral_api_key, timeout_ms=30000, retry_config=None)
    result = run(query, partial(lexical_search, es=es, index=settings.elasticsearch_index),
                 partial(semantic_search, es=es, index=settings.elasticsearch_index), es, settings.elasticsearch_index,
                 mistral, settings.mistral_chat_model, snapshot, lessons_path=LESSONS_PATH)
    display(result)
    if args.save:
        Path(args.save).write_text(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
