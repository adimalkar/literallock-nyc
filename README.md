# LiteralLock NYC

Restaurant inspection evidence tied to native CAMIS and a selected date/type.
Python terminal demo using Elasticsearch retrieval and Mistral answering.

## Current status

Both tracks are implemented and connected to live services. The frozen snapshot
contains 407 unique rows, 70 restaurants, and 122 indexed groups (zero indexing
failures). It is a bounded sample, not citywide coverage. 40 focused logic
checks pass (`test_core.py`, `test_ingest.py`); unit fixtures are labeled synthetic
rows, not NYC source data or accuracy measurements.

Five selected live cases were inspected on October 7, 2026:

| Case | Retained claims | Elastic / chat calls | Seconds |
|---|---:|---:|---:|
| SUBWAY 41367337 (Bronx), 2026-10-05; same-CAMIS 2024-03-08 group ranked lexical #1 and SUBWAY 41456579 (Brooklyn) both excluded | 4 | 2 / 1 | 9.061 |
| PARIS BAGUETTE 41521360, 2024-06-11, calorie posting | 1 | 2 / 1 | 8.490 |
| Same restaurant/day, cycle inspection; wrong types/dates excluded | 4 | 2 / 1 | 9.730 |
| Same CAMIS, 2024-01-01, verified absent indexed scope | 0 | 3 / 0 | 0.461 |
| Conceptual pest paraphrase; four separately scoped examples | 4 | 2 / 1 | 9.444 |

Supported findings remain `partial` because coverage is bounded. Absence remains
`insufficient`. The first distractor/conceptual runs rejected altered apostrophes
in quotes. The prompt was improved and those cases rerun as separate evaluation
runs; normal answering still contains no automatic repair/retry loop. Original
and revised outputs are retained. This is a four-case smoke check, not a benchmark.
The configured chat model used was `codestral-latest`; embeddings use `mistral-embed`.

## Live interactive demo

```bash
.venv/bin/python live.py                 # prompts: question, CAMIS (name matches listed), date, type / borough
.venv/bin/python live.py 'What violations were recorded at Subway?' --camis 41456579 --date 2026-10-05
.venv/bin/python live.py --replay data/subway_exact_run.json    # same visuals offline (stage fallback)
```

Shows each pipeline stage as it runs: scope lock and routing profile, both Elastic
rankings (mismatching CAMIS/date in red), the deterministic evidence gate with a
reason per candidate, exact recovery if triggered, the bundle sent to Mistral,
mechanical validation of the model's claims, then the answer with an evidence tag
(CAMIS · name · address · date · type · violation code · raw row ID) under each claim.
This is an execution trace of what the code did, not hidden model reasoning.
Typing a restaurant name lists every CAMIS sharing it; identity is chosen, never inferred.
Additional live runs: `data/live_subway_brooklyn_run.json`, `data/live_conceptual_queens_run.json`.

## Identity resolution (no model)

`resolve.py` maps a typed name to CAMIS only from words the user actually typed: if a name matches several
CAMIS values, a candidate survives only if its borough, ZIP, or street core name appears in the question.
Exactly one survivor is *proposed* for confirmation; otherwise identity stays ambiguous and the user chooses.
Example: "Subway on Church Avenue" → 41456579 (Brooklyn); "Convene in Manhattan" stays ambiguous (both are).
Retrieval still does not trust identity: even with the CAMIS selected, the semantic #1 result can be the other
SUBWAY; the code gate excludes it.

One-line mode: `.venv/bin/python live.py --auto` — type only a question. `resolve.auto_scope` takes identity
(name + typed street/borough/ZIP), date (explicit date in the question, else latest in this snapshot, labeled)
and borough (only if typed) from the user's words and prints each decision as STEP 0. An ambiguous name is not
answered and makes no Elastic or Mistral call. An explicit date absent from the snapshot is kept, so exact
recovery reports the gap instead of silently switching dates.

## Bounded self-correction

- Run time: if code validation removes any Mistral claim, the rejection reasons plus fixed rules are sent back
  for **one** retry (chat budget 1 + at most 1). The retry must pass the same checks; the attempt with more
  verified claims is kept. Malformed output is never retried.
- Across runs: `data/lessons.json` counts validator rejection codes; each seen code adds a fixed, code-written
  rule to later prompts (model text never becomes a lesson). Seeded from tonight's two real `QUOTE_MISSING`
  rejections. Effect on answer quality is not measured; this is not an autonomous self-improving system.
- Mechanism proof: `.venv/bin/python -m pytest -k retry -v`.

## Presentation (offline, recorded runs)

```bash
.venv/bin/python present.py              # intro + four scenes, Enter between scenes
.venv/bin/python present.py intro        # dataset / index / sponsor integration
.venv/bin/python present.py data/subway_exact_run.json   # one compact scene
.venv/bin/python demo.py --replay data/subway_exact_run.json   # full audit for the same run
```

Scenes: `data/subway_exact_run.json`, `data/codex_reviewed_distractor_run.json`,
`data/codex_reviewed_absent_run.json`, `data/codex_reviewed_conceptual_run.json`.
★ = sent to Mistral, · = admissible but over the four-group cap, ✘ = rejected by code.

## Run after ingestion is ready

Use the existing `.venv` and configured `.env`; do not print credentials.

```bash
.venv/bin/python demo.py --help
.venv/bin/python -m pytest -q
.venv/bin/python demo.py 'Explain the recorded violations' --camis 41521360 --date 2024-06-11 --type 'Calorie Posting / Initial Inspection' --save data/exact_run.json
.venv/bin/python demo.py 'Which sampled inspections describe pest evidence around food preparation?' --save data/conceptual_run.json
.venv/bin/python demo.py --replay data/codex_exact_run.json
```

Identifiers and dates must be selected from the actual snapshot. Omit `--type`
to inspect separate type groups on the selected day. `--source-only` disables
chat for that run. Replay is explicitly recorded prior output and makes no
network calls. `ingest.py` supports `fetch`, `build`, `index`, and `all`;
the existing snapshot is frozen and ready, so demo runs do not require ingestion.

## Data and index

- `data/raw_rows.json`: 491 raw rows from two bounded fetches of `43nn-pn8j`
  (250 most recent rows citywide, cap reached; history since 2024-01-01 for 15
  deterministically selected CAMIS values, 241 rows, cap 250 not reached). 407 unique.
- `data/snapshot.json`: parameters, retrieval time, checksum, counts, coverage notes,
  ingestion outcome. `data/groups.json`: the 122 indexed groups with raw rows.
- `data/scenarios.json`: real scopes used for evaluation, with verified exact-lookup counts.
- Index `literallock_nyc_inspections` (Elastic Serverless 9.6.0, project-owned, never
  deleted by code). Keyword `camis`, `inspection_date_key`, `inspection_type`, `boro`;
  text `title`/`body` for BM25; `body_semantic` is `semantic_text` on inference endpoint
  `literallock-embeddings` (service `mistral`, model `mistral-embed`, 1024 dims).
- Elastic `_id` = stable group ID; row IDs are SHA-256 fingerprints of canonical raw rows
  (application citation keys, not agency IDs).

## Evidence contract

- Native CAMIS strings establish restaurant identity; names alone do not.
- Separate six-hit lexical and semantic lists remain visible.
- Automatic weighted RRF uses 0.7/0.3 for entity mode, 0.3/0.7 for conceptual
  mode, and rank constant 60. These are heuristics, not measured optimal weights.
- Code excludes wrong CAMIS/date/type before supplying evidence to Mistral.
- At most four groups and 24,000 source-body characters enter the bundle.
- Recovery is one exact lookup round, currently one extra Elastic request;
  the specification allows up to two. Empty and failed lookups differ.
- Normal answering uses one structured Mistral call, plus at most one validator-feedback retry.
- Citation IDs, exact body excerpts, claim scope, available row provenance,
  conflicting scalar fields, and completeness language are checked.
- Invalid output falls back to visible source evidence without a repair call.

Snapshot manifests disclose sampling, retrieval parameters, and coverage.
Grouping by `(camis, inspection_date, inspection_type)` is an application
convention, not a verified unique agency inspection identifier. Recorded
historical findings do not establish current safety. Quote matching and scope
checks do not prove semantic entailment; demo claims require manual review.
The pipeline starts no further work after its 60-second elapsed budget;
remote timeout behavior and transport retries may exceed that wall time.
Logical request counters are shown, not claimed provider inference totals.

Laya, Streamlit, Agent Builder, and comparison automation are disabled or
unimplemented. Source plans are preserved.

## Attribution

Data: NYC DOHMH New York City Restaurant Inspection Results, dataset `43nn-pn8j`.
Starter: [AvenueJ/elastic-mistral-hacknight](https://github.com/AvenueJ/elastic-mistral-hacknight).
Retained raw rows and snapshot manifest are the source of actual run provenance.

## Three-minute presentation

1. 0:00–0:20: a name is not an identity; `present.py intro` (NYC rows, Elastic mapping, Mistral).
2. 0:20–1:10: SUBWAY scene — wrong date ranks lexical #1, other SUBWAY rejected, cited answer.
3. 1:10–1:35: PARIS BAGUETTE — same restaurant and day, different type rejected.
4. 1:35–2:00: absent indexed scope; one exact lookup, no chat call, explicit gap.
5. 2:00–2:30: conceptual paraphrase; automatic 0.3/0.7 profile; separate identities.
6. 2:30–3:00: counters, excerpts, snapshot limitations, what is and is not claimed.

Use clearly labeled saved actual runs if stage connectivity fails. Do not claim
general accuracy, present-day restaurant status, or that a baseline must fail.
