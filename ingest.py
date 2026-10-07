"""Fetch a small real DOHMH snapshot, group rows deterministically, and index them.

Usage:
  python ingest.py fetch [--force]   # bounded NYC Open Data fetch -> data/raw_rows.json
  python ingest.py build             # raw rows -> data/groups.json + data/snapshot.json
  python ingest.py index             # create owned index if absent, bulk index groups
  python ingest.py all               # fetch (if absent), build, index

Never deletes an index. Refuses to write into an index not tagged as project-owned.
"""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests

from config import (DATA_DIR, DATASET_ID, GROUPS_PATH, PROJECT_OWNER_TAG, RAW_ROWS_PATH,
                    SNAPSHOT_PATH, SOURCE_URL, STARTER_REVISION, get_es, get_settings)

RECENT_LIMIT = 250          # fetch 1: most recent violation rows citywide
HISTORY_LIMIT = 250         # fetch 2: history for selected CAMIS values
HISTORY_CAMIS_MAX = 15
HISTORY_SINCE = "2024-01-01T00:00:00"
PEST_CODES = {"04K", "04L", "04M", "04N", "04O", "08A"}


# ---------------------------------------------------------------- fetch

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get(params, timeout):
    resp = requests.get(SOURCE_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected Socrata payload type {type(rows).__name__}")
    return rows


def _norm_name(dba):
    return re.sub(r"[^A-Z0-9]+", " ", (dba or "").upper()).strip()


def select_history_camis(rows):
    """Deterministic CAMIS selection from fetch 1 for a bounded history fetch.

    Priority: (a) names shared by several CAMIS values (identity distractors),
    (b) restaurants with pest-related violation codes (conceptual queries),
    (c) remaining CAMIS by row count. Ties break on CAMIS string.
    """
    by_name = defaultdict(set)
    rows_per_camis = Counter()
    pest = set()
    for r in rows:
        c = r.get("camis")
        if not c:
            continue
        by_name[_norm_name(r.get("dba"))].add(c)
        rows_per_camis[c] += 1
        if r.get("violation_code") in PEST_CODES:
            pest.add(c)
    chosen = []
    for name in sorted(n for n, cs in by_name.items() if len(cs) > 1 and n):
        for c in sorted(by_name[name])[:2]:
            if c not in chosen:
                chosen.append(c)
        if len(chosen) >= 6:
            break
    for c in sorted(pest, key=lambda c: (-rows_per_camis[c], c)):
        if len(chosen) >= 11:
            break
        if c not in chosen:
            chosen.append(c)
    for c in sorted(rows_per_camis, key=lambda c: (-rows_per_camis[c], c)):
        if len(chosen) >= HISTORY_CAMIS_MAX:
            break
        if c not in chosen:
            chosen.append(c)
    return chosen[:HISTORY_CAMIS_MAX], {n: sorted(cs) for n, cs in by_name.items() if len(cs) > 1}


def fetch(force=False):
    if RAW_ROWS_PATH.exists() and not force:
        print(f"{RAW_ROWS_PATH} exists; frozen snapshot kept (use --force to refetch)")
        return json.loads(RAW_ROWS_PATH.read_text())
    timeout = get_settings().fetch_timeout_s
    DATA_DIR.mkdir(exist_ok=True)
    fetches = []

    p1 = {"$limit": RECENT_LIMIT,
          "$where": "camis IS NOT NULL AND inspection_date IS NOT NULL",
          "$order": "inspection_date DESC, camis ASC, violation_code ASC"}
    t1 = _now()
    rows1 = _get(p1, timeout)
    fetches.append({"name": "recent", "url": SOURCE_URL, "params": p1, "retrieved_at": t1,
                    "row_count": len(rows1), "limit": RECENT_LIMIT,
                    "cap_reached": len(rows1) >= RECENT_LIMIT})

    camis_list, shared_names = select_history_camis(rows1)
    cutoff = max(r["inspection_date"] for r in rows1)
    in_list = ",".join("'" + c.replace("'", "") + "'" for c in camis_list)
    p2 = {"$limit": HISTORY_LIMIT,
          "$where": f"camis IN ({in_list}) AND inspection_date >= '{HISTORY_SINCE}' AND inspection_date <= '{cutoff}'",
          "$order": "inspection_date DESC, camis ASC, violation_code ASC"}
    t2 = _now()
    rows2 = _get(p2, timeout)
    fetches.append({"name": "selected_history", "url": SOURCE_URL, "params": p2, "retrieved_at": t2,
                    "row_count": len(rows2), "limit": HISTORY_LIMIT,
                    "cap_reached": len(rows2) >= HISTORY_LIMIT,
                    "selected_camis": camis_list, "shared_name_camis_in_recent": shared_names})

    rows = [{"fetch": "recent", "raw": r} for r in rows1] + [{"fetch": "selected_history", "raw": r} for r in rows2]
    payload = {"dataset_id": DATASET_ID, "source_url": SOURCE_URL, "starter_revision": STARTER_REVISION,
               "fetches": fetches, "rows": rows}
    RAW_ROWS_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    for f in fetches:
        print(f"fetch {f['name']}: {f['row_count']} rows (limit {f['limit']}, cap_reached={f['cap_reached']})")
    return payload


# ---------------------------------------------------------------- build

def row_fingerprint(raw):
    canon = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "r-" + hashlib.sha256(f"{DATASET_ID}|{canon}".encode()).hexdigest()[:16]


def date_key(value):
    """Return (YYYY-MM-DD or None, valid flag). 1900-01-01 marks 'not yet inspected' in this dataset."""
    if not value:
        return None, False
    try:
        d = datetime.fromisoformat(value.replace("Z", "")).date()
    except ValueError:
        return None, False
    return d.isoformat(), d.year > 1900


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def make_group_id(camis, dkey, itype):
    t = itype if itype is not None else "__missing_type__"
    h = hashlib.sha256(f"{DATASET_ID}|{camis}|{dkey}|{t}".encode()).hexdigest()[:8]
    return f"g-{camis}-{dkey or 'nodate'}-{_slug(t) or 'type'}-{h}"


def _uniq(values):
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _address(r):
    return " ".join(x for x in [r.get("building"), r.get("street")] if x) + (
        f", {r['boro']} {r.get('zipcode', '')}".rstrip() if r.get("boro") else "")


def build_groups(payload):
    snapshot_id = "snap-" + hashlib.sha256(
        json.dumps(sorted(row_fingerprint(x["raw"]) + "|" + x["fetch"] for x in payload["rows"])).encode()
    ).hexdigest()[:12]

    # Deduplicate identical source rows across overlapping fetches; record duplicates.
    unique, fetch_of, dup_counter = {}, defaultdict(list), Counter()
    for item in payload["rows"]:
        rid = row_fingerprint(item["raw"])
        fetch_of[rid].append(item["fetch"])
        if rid in unique:
            dup_counter[rid] += 1
        else:
            unique[rid] = item["raw"]
    in_fetch_dups = {rid: n for rid, n in dup_counter.items() if len(set(fetch_of[rid])) < len(fetch_of[rid])}

    # Coverage: fetches are ordered (inspection_date DESC, camis ASC, ...), so a capped fetch can
    # only have cut rows of its final (date, camis) pair. Relies on Socrata honouring $order.
    capped = {}
    for f in payload["fetches"]:
        if f["cap_reached"]:
            last = [x["raw"] for x in payload["rows"] if x["fetch"] == f["name"]][-1]
            capped[f["name"]] = (date_key(last.get("inspection_date"))[0], last.get("camis"))
    history = next((f for f in payload["fetches"] if f["name"] == "selected_history"), None)
    history_camis = set(history["selected_camis"]) if history else set()

    buckets = defaultdict(list)
    for rid, raw in unique.items():
        dkey, _ = date_key(raw.get("inspection_date"))
        buckets[(raw.get("camis"), dkey, raw.get("inspection_type"))].append((rid, raw))

    groups = []
    for (camis, dkey, itype), members in buckets.items():
        members.sort(key=lambda m: (m[1].get("violation_code") or "", m[0]))
        raws = [m[1] for m in members]
        _, date_valid = date_key(raws[0].get("inspection_date"))
        conflicts = []
        scalar = {}
        for field in ["dba", "boro", "building", "street", "zipcode", "cuisine_description",
                      "score", "grade", "grade_date", "action", "inspection_date", "record_date", "phone"]:
            vals = _uniq([r.get(field) for r in raws])
            scalar[field] = vals
            if len([v for v in vals if v is not None]) > 1 and field not in ("record_date",):
                conflicts.append(field)

        flags = []
        if not camis or not isinstance(camis, str):
            scope_status = "unknown"; flags.append("missing_camis")
        elif not dkey or not date_valid:
            scope_status = "unknown"; flags.append("invalid_or_placeholder_date")
        elif itype is None:
            scope_status = "unknown"; flags.append("missing_inspection_type")
        elif any(c in conflicts for c in ("score", "grade", "action", "dba")):
            scope_status = "ambiguous"; flags.append("conflicting_scalar_fields")
        else:
            scope_status = "valid"

        # Coverage of this group within the snapshot.
        coverage = "complete_within_snapshot_rows"
        source_fetches = sorted({f for rid, _ in members for f in fetch_of[rid]})
        for fname, boundary in capped.items():
            if (dkey, camis) == boundary and fname in source_fetches:
                fully_refetched = (fname == "recent" and camis in history_camis and dkey >= HISTORY_SINCE[:10]
                                   and "selected_history" not in capped)
                if not fully_refetched:
                    coverage = "possibly_truncated_at_fetch_cap"

        violations = []
        for rid, r in members:
            if r.get("violation_code") or r.get("violation_description"):
                violations.append({"row_id": rid, "violation_code": r.get("violation_code"),
                                   "violation_description": r.get("violation_description"),
                                   "critical_flag": r.get("critical_flag")})

        dba = next((v for v in scalar["dba"] if v), "")
        boro = next((v for v in scalar["boro"] if v), None)
        address = _address(raws[0])
        score_txt = " | ".join(str(v) for v in scalar["score"] if v is not None) or "not recorded"
        grade_txt = " | ".join(str(v) for v in scalar["grade"] if v is not None) or "not recorded"
        action_txt = " | ".join(str(v) for v in scalar["action"] if v is not None) or "not recorded"
        title = f"CAMIS {camis} | {dba} | {address} | {dkey or 'unknown date'} | {itype or 'unknown type'}"
        lines = [
            f"CAMIS: {camis}",
            f"Restaurant name (DBA): {dba}" + (f" (variants: {', '.join(v for v in scalar['dba'] if v)})" if "dba" in conflicts else ""),
            f"Address: {address}",
            f"Cuisine: {next((v for v in scalar['cuisine_description'] if v), 'not recorded')}",
            f"Inspection date: {dkey or 'unknown'}",
            f"Inspection type: {itype or 'not recorded'}",
            f"Action: {action_txt}",
            f"Score: {score_txt}",
            f"Grade: {grade_txt}",
            f"Violations recorded in snapshot ({len(violations)}):",
        ]
        for v in violations:
            crit = f" [{v['critical_flag']}]" if v.get("critical_flag") else ""
            lines.append(f"- {v['violation_code'] or 'no code'}{crit}: {v['violation_description'] or 'no description recorded'}")
        if not violations:
            lines.append("- none recorded in these rows")
        body = "\n".join(lines)

        groups.append({
            "group_id": make_group_id(camis, dkey, itype),
            "snapshot_id": snapshot_id,
            "dataset_id": DATASET_ID,
            "camis": camis,
            "dba": dba,
            "dba_values": [v for v in scalar["dba"] if v],
            "boro": boro,
            "boro_values": [v for v in scalar["boro"] if v],
            "building": next((v for v in scalar["building"] if v), None),
            "street": next((v for v in scalar["street"] if v), None),
            "zipcode": next((v for v in scalar["zipcode"] if v), None),
            "address": address,
            "cuisine_description": next((v for v in scalar["cuisine_description"] if v), None),
            "inspection_date": dkey if date_valid else None,
            "inspection_date_raw_values": scalar["inspection_date"],
            "inspection_date_key": dkey,
            "inspection_type": itype,
            "score_values": [v for v in scalar["score"] if v is not None],
            "grade_values": [v for v in scalar["grade"] if v is not None],
            "grade_date_values": [v for v in scalar["grade_date"] if v is not None],
            "action_values": [v for v in scalar["action"] if v is not None],
            "violation_codes": [v["violation_code"] for v in violations if v["violation_code"]],
            "critical_flags": _uniq([v["critical_flag"] for v in violations if v["critical_flag"]]),
            "violations": violations,
            "raw_row_ids": [rid for rid, _ in members],
            "raw_rows": [{"row_id": rid, "fetches": sorted(set(fetch_of[rid])), "fields": r} for rid, r in members],
            "row_count": len(members),
            "source_fetches": source_fetches,
            "conflicts": conflicts,
            "flags": flags,
            "scope_status": scope_status,
            "coverage": coverage,
            "title": title,
            "body": body,
            "body_semantic": body,
            "grouping_convention": "(camis, inspection_date day, inspection_type); application grouping, not an agency inspection ID",
        })
    groups.sort(key=lambda g: g["group_id"])
    ids = [g["group_id"] for g in groups]
    assert len(ids) == len(set(ids)), "group_id collision"

    status_counts = Counter(g["scope_status"] for g in groups)
    coverage_counts = Counter(g["coverage"] for g in groups)
    manifest = {
        "dataset_id": DATASET_ID,
        "source_url": payload["source_url"],
        "starter_revision": payload["starter_revision"],
        "fetch_parameters": payload["fetches"],
        "retrieved_at": payload["fetches"][0]["retrieved_at"],
        "snapshot_id": snapshot_id,
        "raw_rows_sha256": hashlib.sha256(RAW_ROWS_PATH.read_bytes()).hexdigest() if RAW_ROWS_PATH.exists() else None,
        "raw_row_count": len(payload["rows"]),
        "unique_raw_row_count": len(unique),
        "cross_fetch_duplicate_rows": sum(dup_counter.values()) - sum(in_fetch_dups.values()),
        "identical_rows_within_a_fetch": in_fetch_dups,
        "unique_camis_count": len({g["camis"] for g in groups}),
        "inspection_group_count": len(groups),
        "scope_status_counts": dict(status_counts),
        "coverage_counts": dict(coverage_counts),
        "cap_reached": any(f["cap_reached"] for f in payload["fetches"]),
        "coverage_notes": [
            f"Bounded sample: {RECENT_LIMIT} most recent citywide rows plus capped history "
            f"(since {HISTORY_SINCE[:10]}) for {len(history_camis)} selected CAMIS values. Not full-city coverage.",
            "The final (date, CAMIS) pair of a capped fetch may be missing rows (coverage=possibly_truncated_at_fetch_cap).",
            "Other restaurants inspected on the recent fetch's oldest date exist citywide but are absent from this sample.",
            "An empty lookup means no matching records in this snapshot, not that NYC has no such inspection.",
            "Grades/scores are recorded snapshot fields, not current restaurant status.",
        ],
        "grouping_convention": "(camis, inspection_date day, inspection_type)",
        "ingestion_outcome": None,
    }
    return groups, manifest


def build():
    payload = json.loads(RAW_ROWS_PATH.read_text())
    groups, manifest = build_groups(payload)
    if SNAPSHOT_PATH.exists():
        old = json.loads(SNAPSHOT_PATH.read_text())
        if old.get("snapshot_id") == manifest["snapshot_id"]:
            manifest["ingestion_outcome"] = old.get("ingestion_outcome")
    GROUPS_PATH.write_text(json.dumps(groups, indent=1, ensure_ascii=False))
    SNAPSHOT_PATH.write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"snapshot {manifest['snapshot_id']}: {manifest['unique_raw_row_count']} unique rows, "
          f"{manifest['unique_camis_count']} CAMIS, {manifest['inspection_group_count']} groups, "
          f"scope={manifest['scope_status_counts']} coverage={manifest['coverage_counts']}")
    return groups, manifest


# ---------------------------------------------------------------- index

def mapping(inference_id):
    kw = {"type": "keyword"}
    return {
        "_meta": {"owner": PROJECT_OWNER_TAG, "dataset_id": DATASET_ID},
        "dynamic": "false",
        "properties": {
            "group_id": kw, "snapshot_id": kw, "dataset_id": kw, "camis": kw,
            "dba": {"type": "text", "fields": {"raw": kw}}, "dba_values": kw,
            "boro": kw, "boro_values": kw, "building": kw, "street": kw, "zipcode": kw,
            "address": {"type": "text"}, "cuisine_description": kw,
            "inspection_date": {"type": "date", "format": "strict_date"},
            "inspection_date_key": kw, "inspection_type": kw,
            "score_values": kw, "grade_values": kw, "action_values": kw,
            "violation_codes": kw, "critical_flags": kw,
            "row_count": {"type": "integer"},
            "conflicts": kw, "flags": kw, "scope_status": kw, "coverage": kw,
            "title": {"type": "text"}, "body": {"type": "text"},
            "body_semantic": {"type": "semantic_text", "inference_id": inference_id},
            # Stored-only provenance (in _source, not indexed): violations, raw_rows, etc.
        },
    }


def index_groups():
    from elasticsearch import helpers

    s = get_settings()
    es = get_es(s)
    groups = json.loads(GROUPS_PATH.read_text())
    manifest = json.loads(SNAPSHOT_PATH.read_text())
    idx = s.elasticsearch_index
    if es.indices.exists(index=idx):
        meta = es.indices.get_mapping(index=idx)[idx]["mappings"].get("_meta", {})
        if meta.get("owner") != PROJECT_OWNER_TAG:
            raise SystemExit(f"Index {idx} exists and is not tagged owner={PROJECT_OWNER_TAG}; refusing to write.")
        print(f"reusing owned index {idx}")
    else:
        es.indices.create(index=idx, mappings=mapping(s.inference_id))
        print(f"created index {idx} (semantic_text via {s.inference_id})")

    actions = [{"_op_type": "index", "_index": idx, "_id": g["group_id"], "_source": g} for g in groups]
    ok, errors = helpers.bulk(es.options(request_timeout=120), actions, chunk_size=50,
                              raise_on_error=False, raise_on_exception=False)
    failed = [{"id": e.get("index", {}).get("_id"), "error": str(e.get("index", {}).get("error"))[:300]} for e in errors]
    es.indices.refresh(index=idx)
    snap_count = es.count(index=idx, query={"term": {"snapshot_id": manifest["snapshot_id"]}})["count"]
    total_count = es.count(index=idx)["count"]
    outcome = {"index": idx, "inference_id": s.inference_id, "semantic_backend": "semantic_text",
               "attempted": len(actions), "indexed": ok, "failed": len(failed), "failures": failed[:20],
               "docs_for_snapshot_in_index": snap_count, "docs_in_index_total": total_count,
               "complete": snap_count == len(groups) and not failed, "indexed_at": _now()}
    manifest["ingestion_outcome"] = outcome
    SNAPSHOT_PATH.write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in outcome.items() if k != "failures"}))
    for f in failed[:5]:
        print("FAILED", f)
    if total_count != snap_count:
        print(f"WARNING: index holds {total_count - snap_count} docs from another snapshot")
    return outcome


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "fetch":
        fetch(force="--force" in sys.argv)
    elif cmd == "build":
        build()
    elif cmd == "index":
        index_groups()
    elif cmd == "all":
        fetch()
        build()
        index_groups()
    else:
        raise SystemExit(__doc__)
