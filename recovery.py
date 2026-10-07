"""One deterministic exact lookup round; no model-directed expansion."""
from contracts import InspectionGroup
from scope import admission


def recover(es, index, query, counters):
    if not query.camis:
        return [], [], None
    if counters["recovery_rounds"] >= 1:
        raise RuntimeError("Recovery round budget exhausted")
    counters["recovery_rounds"] += 1
    counters["recovery_logical"] += 1
    counters["elastic_logical"] += 1
    filters = [{"term": {"camis": query.camis}}, {"term": {"inspection_date_key": query.inspection_date_key}}]
    if query.inspection_type is not None:
        filters.append({"term": {"inspection_type": query.inspection_type}})
    if query.borough:
        filters.append({"term": {"boro": query.borough}})
    try:
        response = es.search(index=index, size=4, query={"bool": {"filter": filters}},
                             track_total_hits=True, source_excludes=["body_semantic"])
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]
        total = total["value"] if isinstance(total, dict) else total
        groups = [InspectionGroup.model_validate(h["_source"]) for h in hits]
        rows = []
        for group in groups:
            accepted, reason = admission(query, group)
            rows.append({"group_id": group.group_id, "group": group, "accepted": accepted,
                         "reason": reason, "provenance": "exact_recovery", "fused_score": 0.0})
        gaps = [] if hits else ["LOOKUP_EMPTY: no matching evidence in the indexed snapshot"]
        if total > len(hits):
            gaps.append(f"CONTEXT_EXCERPTED: exact scope has {total} groups; only {len(hits)} returned")
        return rows, gaps, total
    except Exception as exc:
        return [], [f"LOOKUP_FAILED: exact lookup failed ({type(exc).__name__}); absence is unproven"], None
