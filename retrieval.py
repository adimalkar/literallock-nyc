"""Independent lexical and semantic Elastic branches. No fusion here (see evidence.fuse).

Each function issues exactly one logical Elastic search request and returns a list of
contracts.SearchHit with 1-based branch rank, the branch's own score, and the full group
metadata from _source (embeddings excluded). Ranking never authorizes evidence; admission
is enforced separately by scope.admission.

Borough: an explicit QuerySpec.borough filter is applied identically to both branches in
conceptual mode. In entity mode the branches stay broad (identity/date are admission gates),
so wrong-entity/date candidates remain visible for the audit.
"""
import time

from contracts import InspectionGroup, SearchHit
from config import get_es, get_settings

_SOURCE_EXCLUDES = ["body_semantic"]
LAST_TRACE: dict = {}


def _get(spec, name, default=None):
    if isinstance(spec, dict):
        return spec.get(name, default)
    return getattr(spec, name, default)


def _filters(spec):
    borough = _get(spec, "borough")
    if borough and not _get(spec, "camis"):
        return [{"term": {"boro": borough}}]
    return []


def _run(branch, query, spec, limit, es, index):
    s = get_settings()
    es = es or get_es(s)
    index = index or s.elasticsearch_index
    t0 = time.perf_counter()
    resp = es.search(index=index, size=limit, query=query, source_excludes=_SOURCE_EXCLUDES,
                     track_total_hits=True)
    elapsed = time.perf_counter() - t0
    hits = []
    for rank, h in enumerate(resp["hits"]["hits"], start=1):
        group = InspectionGroup.model_validate(h["_source"])
        if group.group_id != h["_id"]:
            raise ValueError(f"Document _id {h['_id']} does not match group_id {group.group_id}")
        hits.append(SearchHit(group_id=h["_id"], rank=rank, score=h.get("_score"), group=group,
                              branch=branch))
    total = resp["hits"]["total"]
    LAST_TRACE[branch] = {"index": index, "limit": limit, "returned": len(hits),
                          "total_hits": total["value"] if isinstance(total, dict) else total,
                          "elastic_took_ms": resp.get("took"), "wall_s": round(elapsed, 3),
                          "filters": _filters(spec), "logical_requests": 1}
    return hits


def lexical_search(query_spec, limit=6, es=None, index=None):
    """BM25 over title/body. With an explicit CAMIS/date, exact keyword matches add a
    ranking boost only (should clauses); they are not filters."""
    question = _get(query_spec, "question")
    should = [{"multi_match": {"query": question, "fields": ["title^2", "body", "dba^2", "address"]}}]
    camis = _get(query_spec, "camis")
    if camis:
        should.append({"term": {"camis": {"value": camis, "boost": 5.0}}})
        if _get(query_spec, "inspection_date_key"):
            should.append({"term": {"inspection_date_key": {"value": _get(query_spec, "inspection_date_key"), "boost": 2.0}}})
    query = {"bool": {"should": should, "minimum_should_match": 1, "filter": _filters(query_spec)}}
    return _run("lexical", query, query_spec, limit, es, index)


def semantic_search(query_spec, limit=6, es=None, index=None):
    """Mistral-embedded semantic_text query over body_semantic (inference at query time)."""
    query = {"bool": {"must": [{"semantic": {"field": "body_semantic", "query": _get(query_spec, "question")}}],
                      "filter": _filters(query_spec)}}
    return _run("semantic", query, query_spec, limit, es, index)
