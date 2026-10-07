## Inspiration
NYC has many restaurants that share a name. Our snapshot alone has two different SUBWAY locations inspected on the same day. Search ranks by similarity, so a typical RAG pipeline can mix up two restaurants, or a 2024 inspection with this week's, and then confidently cite the wrong one. We wanted answers you can trust because the evidence is checked, not because the model sounds sure.

## What it does
Ask a question in plain English about NYC restaurant inspections:
- **Identity comes from your words, resolved by code.** "Subway on Church Avenue" or "the Bronx Subway" locks a single native CAMIS ID. A vague "Subway" is never guessed: each matching restaurant is answered separately, and evidence is never merged.
- **Two independent Elasticsearch rankings**, BM25 and semantic, are fused with weighted reciprocal-rank fusion. The weights are set automatically: 0.7/0.3 for a specific restaurant, 0.3/0.7 for conceptual questions.
- **A deterministic evidence gate** rejects every candidate from the wrong restaurant (CAMIS), wrong date or wrong inspection type before the LLM sees anything. In our live runs, the #1 result from a ranking was often the wrong restaurant or the wrong date; the gate removes it and shows why.
- **Bounded recovery:** if the selected inspection is missing, one exact lookup runs. Empty means "not in this snapshot", never "it didn't happen", and no answer is generated.
- **One structured Mistral call** writes the answer. Code then checks every claim: the citation exists, the quote is verbatim, and the CAMIS, date and type are in scope. Failing claims are removed and shown, with at most one retry using the validator's feedback. A ledger records rejection types as fixed, code-written rules for later runs.
- **Every claim carries an evidence tag:** CAMIS · name · address · date · inspection type · violation code · source row ID.

## How we built it
- **Data:** a bounded, real snapshot of NYC Open Data DOHMH Restaurant Inspection Results (`43nn-pn8j`). 491 rows were fetched and 407 were unique, covering 70 restaurants grouped into 122 inspection groups by (CAMIS, date, inspection type). Raw rows, fetch parameters, timestamp and checksum are preserved.
- **Elastic:** Elasticsearch Serverless 9.6. The project-owned index `literallock_nyc_inspections` has keyword identity fields, BM25 text fields, and a `semantic_text` field backed by an Elastic inference endpoint.
- **Mistral:** `mistral-embed` (1024 dimensions) through the Elastic inference endpoint `literallock-embeddings` for semantic search, and the Mistral chat API (`codestral-latest`) with structured output for the single answer call.
- **App:** Python pipeline, a Streamlit chat UI showing a live evidence trace, a terminal mode, and a static landing board of the 70 restaurants with "same name" highlighting. 55 automated tests.

## Challenges we ran into
- The model normalized curly apostrophes, so verbatim-quote checks failed. We now give it exact excerpt options and record the failure as a lesson.
- Our validator wrongly flagged real violation text ("cleaning on all sides") as a completeness claim. We fixed it to ignore verified quotes and added a regression test.
- Answering two same-name restaurants concurrently caused a race on the lessons ledger, which we fixed with locking and a concurrency test.

## Accomplishments that we're proud of
- On real NYC data, a vague question gets an honest answer, split by restaurant, without guessing.
- The trace shows what the code actually did at each step: both rankings, every rejection reason, the bundle sent to Mistral, and the validation result.
- Absence is reported honestly: "not in this snapshot" with no LLM call.

## What we learned
Retrieval rank is not identity. Even with the right restaurant ID selected, semantic search ranked the other SUBWAY first. Trust has to come from deterministic checks on native identifiers and dates, with the model doing only the writing.

## What's next
- A larger snapshot, and Elastic Agent Builder tools that use the same gate.
- Measuring whether the lessons ledger reduces rejections over time (not measured yet).
- Address-level identity resolution on the full dataset.

## Limitations (stated honestly)
This is a bounded sample, not citywide coverage. Grades are recorded fields, not current safety status. Quote and scope checks do not prove semantic entailment, and the self-correction is bounded rather than an autonomous self-improving loop.

