"""Resolver checks against the frozen real snapshot catalog (data/groups.json)."""
import json
import pytest
from config import GROUPS_PATH
from resolve import resolve

pytestmark = pytest.mark.skipif(not GROUPS_PATH.exists(), reason="frozen snapshot not present")
G = json.loads(GROUPS_PATH.read_text()) if GROUPS_PATH.exists() else []


@pytest.mark.parametrize("question,status,camis", [
    ("What violations were recorded at Subway?", "ambiguous", None),
    ("What violations were recorded at Subway on Church Avenue?", "resolved_by_attributes", "41456579"),
    ("What did inspectors find at the Subway in Brooklyn?", "resolved_by_attributes", "41456579"),
    ("Subway in the Bronx violations", "resolved_by_attributes", "41367337"),
    ("Paris Baguette in Queens", "resolved_by_attributes", "50077029"),
    ("Convene in Manhattan", "ambiguous", None),             # both locations are in Manhattan
    ("Convene at Rockefeller Plaza", "resolved_by_attributes", "50082702"),
    ("Which inspections mention mice?", "no_name", None),
])
def test_resolution(question, status, camis):
    r = resolve(question, G)
    assert r["status"] == status and r["camis"] == camis


def test_bare_numbers_are_not_identity():
    r = resolve("Subway 11226 10473", G)  # both ZIPs typed -> still ambiguous
    assert r["status"] == "ambiguous"


from resolve import auto_scope


@pytest.mark.parametrize("question,mode,camis,date,borough", [
    ("What violations did the Subway on Church Avenue get?", "entity", "41456579", "2026-10-05", None),
    ("What did inspectors find at the Bronx Subway on October 5?", "entity", "41367337", "2026-10-05", None),
    ("What did inspectors find at the Subway in the Bronx on March 8, 2024?", "entity", "41367337", "2024-03-08", None),
    ("What violations were recorded at Subway?", "ambiguous", None, None, None),
    ("Which Queens restaurants had mice or roaches near food preparation?", "conceptual", None, None, "Queens"),
    ("Which inspections mention mice?", "conceptual", None, None, None),
])
def test_auto_scope(question, mode, camis, date, borough):
    s = auto_scope(question, G)
    assert (s["mode"], s["camis"], s["date"], s["borough"]) == (mode, camis, date, borough)


def test_explicit_absent_date_is_kept_not_silently_replaced():
    s = auto_scope("What did the Bronx Subway get on October 9?", G)
    assert s["camis"] == "41367337" and s["date"] == "2026-10-09"
