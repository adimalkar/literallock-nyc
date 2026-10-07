"""Deterministic name → CAMIS resolution from the user's own words (no model, no embeddings).

A name alone never establishes identity. If several CAMIS values share the name, we look only for
distinguishing attributes the user actually typed: borough, ZIP code, or the street's core name.
Exactly one surviving candidate is *proposed* (the user confirms); otherwise identity stays ambiguous.
"""
import re

BOROUGHS = {"MANHATTAN": "Manhattan", "BROOKLYN": "Brooklyn", "QUEENS": "Queens", "BRONX": "Bronx",
            "STATEN ISLAND": "Staten Island"}
STREET_SUFFIXES = {"AVENUE", "AVE", "STREET", "ST", "BOULEVARD", "BLVD", "ROAD", "RD", "PLACE", "PL", "PLAZA",
                   "PARKWAY", "PKWY", "LANE", "LN", "DRIVE", "DR", "WAY", "COURT", "CT", "TERRACE", "EAST",
                   "WEST", "NORTH", "SOUTH", "E", "W", "N", "S"}


def _norm(text):
    return " " + re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).strip() + " "


def name_matches(question, groups):
    """Distinct CAMIS whose DBA appears as a whole word sequence in the question."""
    q = _norm(question)
    hits = {}
    for g in groups:
        name = _norm(g["dba"]).strip()
        if len(name) >= 4 and f" {name} " in q:
            hits.setdefault(g["camis"], g)
    return hits


def _street_core(street):
    return [t for t in _norm(street).split() if t not in STREET_SUFFIXES and len(t) >= 3 and not t.isdigit()]


def signals(question, g):
    """Attributes of candidate g that the user explicitly typed."""
    q = _norm(question)
    found = []
    boro = (g.get("boro") or "").upper()
    if boro and f" {boro} " in q:
        found.append(f"borough '{g['boro']}'")
    zipcode = g.get("zipcode")
    if zipcode and f" {zipcode} " in q:
        found.append(f"ZIP '{zipcode}'")
    core = _street_core(g.get("street"))
    if core and all(f" {t} " in q for t in core):
        found.append(f"street '{g['street'].strip()}'")
    return found


def resolve(question, groups):
    """Return {status, candidates, camis, evidence}.

    status: no_name | unique_name | resolved_by_attributes | ambiguous
    """
    cands = name_matches(question, groups)
    if not cands:
        return {"status": "no_name", "candidates": {}, "camis": None, "evidence": []}
    if len(cands) == 1:
        camis = next(iter(cands))
        return {"status": "unique_name", "candidates": cands, "camis": camis,
                "evidence": [f"only one CAMIS in this snapshot has the name '{cands[camis]['dba']}'"]}
    scored = {c: signals(question, g) for c, g in cands.items()}
    survivors = [c for c, s in scored.items() if s]
    if len(survivors) == 1:
        c = survivors[0]
        return {"status": "resolved_by_attributes", "candidates": cands, "camis": c,
                "evidence": [f"name '{cands[c]['dba']}' matches {len(cands)} restaurants"]
                + [f"your words matched {s}" for s in scored[c]]}
    return {"status": "ambiguous", "candidates": cands, "camis": None,
            "evidence": [f"name matches {len(cands)} restaurants"]
            + ([f"{len(survivors)} still match your location words"] if survivors else
               ["no borough, ZIP, or street in the question distinguishes them"])}


MONTHS = {m: i for i, m in enumerate(["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
                                       "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], start=1)}


def _dates_in(question):
    """Explicit dates the user typed: ISO YYYY-MM-DD or 'October 5[, 2026]'. Returns (month, day, year|None)."""
    out = [(int(m), int(d), int(y)) for y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", question)]
    for mon, day, year in re.findall(r"\b(January|February|March|April|May|June|July|August|September|October|"
                                     r"November|December)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", question, re.I):
        out.append((MONTHS[mon.upper()], int(day), int(year) if year else None))
    return out


def auto_scope(question, groups):
    """One-line scope from the user's own words. Every decision is recorded in `steps` for display.

    Returns {mode: entity|conceptual|ambiguous, camis, date, borough, resolution, steps[str]}.
    """
    steps = []
    res = resolve(question, groups)
    q = _norm(question)
    if res["status"] == "ambiguous":
        steps += res["evidence"] + ["identity NOT resolved to one → each same-name restaurant answered separately, never merged"]
        return {"mode": "ambiguous", "camis": None, "date": None, "borough": None, "resolution": res, "steps": steps}
    if res["camis"]:
        camis = res["camis"]
        steps += res["evidence"] + [f"→ CAMIS {camis} ({res['candidates'][camis]['dba']}, {res['candidates'][camis]['address']})"]
        dates = sorted({g["inspection_date_key"] for g in groups if g["camis"] == camis}, reverse=True)
        wanted = _dates_in(question)
        date = None
        if wanted:
            mon, day, year = wanted[0]
            match = [d for d in dates if int(d[5:7]) == mon and int(d[8:10]) == day and (year is None or int(d[:4]) == year)]
            if len(match) == 1:
                date = match[0]
                steps.append(f"date {date} taken from your words")
            elif len(match) > 1:
                date = match[0]
                steps.append(f"your date matches {len(match)} years in this snapshot → using the most recent, {date}")
            else:
                # Keep the user's explicit date even if absent; exact recovery then reports the gap honestly.
                year = year or int(max(d for g in groups for d in [g["inspection_date_key"]] if d)[:4])
                date = f"{year:04d}-{mon:02d}-{day:02d}"
                steps.append(f"date {date} taken from your words (year assumed from snapshot if not given); "
                             "not among this restaurant's snapshot dates")
        if date is None:
            date = dates[0] if dates else None
            steps.append(f"no date given → {date}, the latest date in this snapshot for this CAMIS "
                         "(not necessarily the latest city inspection)")
        return {"mode": "entity", "camis": camis, "date": date, "borough": None, "resolution": res, "steps": steps}
    borough = next((v for k, v in BOROUGHS.items() if f" {k} " in q), None)
    steps.append("no restaurant name found → conceptual question across restaurants")
    if borough:
        steps.append(f"borough '{borough}' taken literally from your words → filter on both branches")
    return {"mode": "conceptual", "camis": None, "date": None, "borough": borough, "resolution": res, "steps": steps}
