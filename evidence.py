from contracts import SearchHit
from scope import admission


def fuse(query, lexical, semantic, k=60):
    """Weighted RRF; never mix incomparable raw branch scores."""
    candidates = {}
    for branch, hits, weight in [("lexical", lexical, query.lexical_weight), ("semantic", semantic, query.semantic_weight)]:
        seen = set()
        for hit in hits:
            hit = SearchHit.model_validate(hit)
            if hit.group_id != hit.group.group_id or hit.rank < 1:
                raise ValueError("Invalid retrieval identity/rank")
            if hit.group_id in seen:
                continue
            seen.add(hit.group_id)
            row = candidates.setdefault(hit.group_id, {"group_id": hit.group_id, "group": hit.group,
                                                       "fused_score": 0.0, "provenance": "initial"})
            row[branch + "_rank"] = hit.rank
            row[branch + "_score"] = hit.score
            row["fused_score"] += weight / (k + hit.rank)
    rows = sorted(candidates.values(), key=lambda r: (-r["fused_score"], r["group_id"]))
    for row in rows:
        row["accepted"], row["reason"] = admission(query, row["group"])
    return rows


def select_bundle(candidates, max_groups=4, max_chars=24000):
    bundle, gaps, used = [], [], 0
    eligible = [r for r in candidates if r["accepted"]]
    if len(eligible) > max_groups:
        gaps.append("CONTEXT_EXCERPTED: matching candidate groups exceed the four-group cap")
    for row in eligible[:max_groups]:
        group = row["group"]
        remaining = max_chars - used
        if remaining <= 0:
            gaps.append("CONTEXT_EXCERPTED: text cap reached")
            break
        if len(group.body) > remaining:
            group = group.model_copy(update={"body": group.body[:remaining], "coverage": "excerpted"})
            gaps.append("CONTEXT_EXCERPTED: a source body was truncated")
        bundle.append(group)
        used += len(group.body)
    return bundle, gaps
