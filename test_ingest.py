"""Offline checks of ingest grouping on labeled synthetic rows plus the frozen real snapshot."""
import json

import pytest

import ingest
from config import GROUPS_PATH, SNAPSHOT_PATH


def _payload(rows, cap=False):
    return {"source_url": "test", "starter_revision": "test",
            "fetches": [{"name": "recent", "retrieved_at": "t", "cap_reached": cap}],
            "rows": [{"fetch": "recent", "raw": r} for r in rows]}


def _row(camis="00012345", date="2026-10-05T00:00:00.000", itype="Cycle Inspection / Initial Inspection", **kw):
    base = {"camis": camis, "dba": "TEST DELI", "boro": "Queens", "inspection_date": date,
            "inspection_type": itype, "score": "12", "grade": "A", "action": "Violations were cited"}
    base.update(kw)
    return base


def build(rows, cap=False, monkeypatch=None):
    return ingest.build_groups(_payload(rows, cap))


def test_camis_string_and_leading_zeros_preserved():
    groups, _ = build([_row(violation_code="04L", violation_description="Evidence of mice")])
    assert groups[0]["camis"] == "00012345"
    assert "CAMIS: 00012345" in groups[0]["body"]


def test_repeated_score_is_not_summed_and_lines_group_together():
    rows = [_row(violation_code=c, violation_description=f"desc {c}") for c in ("04L", "06D", "10F")]
    groups, m = build(rows)
    assert len(groups) == 1 and groups[0]["row_count"] == 3
    assert groups[0]["score_values"] == ["12"]
    assert m["inspection_group_count"] == 1


def test_conflicting_scores_mark_group_ambiguous():
    rows = [_row(violation_code="04L", score="12"), _row(violation_code="06D", score="30")]
    groups, _ = build(rows)
    assert groups[0]["scope_status"] == "ambiguous"
    assert set(groups[0]["score_values"]) == {"12", "30"}


def test_same_name_different_camis_never_merge():
    groups, _ = build([_row(camis="11111111"), _row(camis="22222222")])
    assert sorted(g["camis"] for g in groups) == ["11111111", "22222222"]


def test_dates_and_types_split_groups():
    groups, _ = build([_row(), _row(date="2025-01-01T00:00:00.000"), _row(itype="Calorie Posting / Initial Inspection")])
    assert len({g["group_id"] for g in groups}) == 3


def test_placeholder_date_and_missing_type_are_not_valid_scope():
    groups, _ = build([_row(date="1900-01-01T00:00:00.000"), _row(camis="22222222", itype=None)])
    assert all(g["scope_status"] == "unknown" for g in groups)


def test_capped_fetch_flags_only_final_date_camis_pair():
    rows = [_row(camis="11111111"), _row(camis="22222222")]
    groups, _ = build(rows, cap=True)
    cov = {g["camis"]: g["coverage"] for g in groups}
    assert cov == {"11111111": "complete_within_snapshot_rows", "22222222": "possibly_truncated_at_fetch_cap"}


def test_group_id_is_deterministic():
    a, _ = build([_row(violation_code="04L")])
    b, _ = build([_row(violation_code="04L")])
    assert a[0]["group_id"] == b[0]["group_id"]


@pytest.mark.skipif(not GROUPS_PATH.exists(), reason="frozen snapshot not present")
def test_frozen_snapshot_provenance():
    groups = json.loads(GROUPS_PATH.read_text())
    m = json.loads(SNAPSHOT_PATH.read_text())
    assert len({g["group_id"] for g in groups}) == len(groups) == m["inspection_group_count"]
    assert sum(g["row_count"] for g in groups) == m["unique_raw_row_count"]
    for g in groups:
        assert isinstance(g["camis"], str)
        assert g["raw_row_ids"] == [r["row_id"] for r in g["raw_rows"]]
        for r in g["raw_rows"]:
            assert r["row_id"] == ingest.row_fingerprint(r["fields"])
            assert r["fields"]["camis"] == g["camis"]
        for v in g["violations"]:
            if v["violation_description"]:
                assert v["violation_description"] in g["body"]
