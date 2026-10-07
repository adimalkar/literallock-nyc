"""Synthetic unit fixtures test logic only; never presented as NYC evidence."""
import json
from types import SimpleNamespace
import pytest
from contracts import InspectionGroup, SearchHit, ModelAnswer
from scope import make_query, admission
from evidence import fuse, select_bundle
from recovery import recover
from answer import validate_answer
from demo import run


def group(**updates):
    values = dict(group_id="unit-source", camis="00123456", inspection_date_key="2026-01-01",
                  inspection_type="unit-type", body="Recorded unit fixture description.", raw_row_ids=["unit-row"])
    return InspectionGroup(**(values | updates))


def query():
    return make_query("Explain recorded findings", "00123456", "2026-01-01", "unit-type")


def model_answer(**claim_updates):
    claim = dict(text="Recorded unit fixture description.", camis="00123456", inspection_date_key="2026-01-01",
                 inspection_type="unit-type", source_ids=["unit-source"],
                 excerpts=[dict(source_id="unit-source", quote="Recorded unit fixture description.")])
    return ModelAnswer.model_validate(dict(claims=[claim | claim_updates], gaps=[], overall_status="supported",
        assessments=[dict(requirement_id=r, status="supported", source_ids=["unit-source"], reason="fixture") for r in query().requirements]))


@pytest.mark.parametrize("updates,reason", [({"camis": "00123457"}, "ENTITY_MISMATCH"),
    ({"inspection_date_key": "2026-01-02"}, "DATE_MISMATCH"),
    ({"inspection_type": "another"}, "TYPE_MISMATCH"), ({"scope_status": "ambiguous"}, "SCOPE_UNKNOWN")])
def test_exact_scope_overrides_relevance(updates, reason):
    assert admission(query(), group(**updates)) == (False, reason)


def test_identity_and_routing():
    assert query().camis == "00123456"
    assert query().lexical_weight == .7
    assert make_query("pest paraphrase").semantic_weight == .7
    with pytest.raises(ValueError):
        make_query("findings", "00123456")


def test_independent_ranks_and_fusion():
    a, b = group(), group(group_id="b", camis="00123457")
    lexical = [SearchHit(group_id=a.group_id, rank=1, score=100, group=a)]
    semantic = [SearchHit(group_id=b.group_id, rank=1, score=.99, group=b)]
    rows = fuse(query(), lexical, semantic)
    assert rows[0]["fused_score"] == .7 / 61
    assert "semantic_rank" not in rows[0]
    assert select_bundle(rows)[0] == [a]


def test_recovery_is_one_round_and_distinguishes_failure():
    class Elastic:
        def search(self, **kwargs):
            assert kwargs["query"]["bool"]["filter"][0] == {"term": {"camis": "00123456"}}
            return {"hits": {"total": {"value": 0}, "hits": []}}
    counters = dict(recovery_rounds=0, recovery_logical=0, elastic_logical=0)
    rows, gaps, total = recover(Elastic(), "owned", query(), counters)
    assert total == 0 and "LOOKUP_EMPTY" in gaps[0]
    with pytest.raises(RuntimeError):
        recover(Elastic(), "owned", query(), counters)
    class Broken:
        def search(self, **kwargs):
            raise TimeoutError()
    _, gaps, total = recover(Broken(), "owned", query(), dict(recovery_rounds=0, recovery_logical=0, elastic_logical=0))
    assert total is None and "LOOKUP_FAILED" in gaps[0]


@pytest.mark.parametrize("updates", [dict(camis="00123457"), dict(source_ids=["missing"]),
    dict(excerpts=[dict(source_id="unit-source", quote="invented quote")]),
    dict(text="All violations are listed."), dict(text="CAMIS 00123457 recorded this finding.")])
def test_invalid_claims_never_render(updates):
    claims, gaps, status = validate_answer(model_answer(**updates), query(), [group()])
    assert claims == [] and status == "insufficient" and gaps


def test_valid_quote_retained_with_sampling_limit():
    claims, gaps, status = validate_answer(model_answer(), query(), [group()])
    assert len(claims) == 1 and status == "partial"
    assert any("SNAPSHOT_LIMITED" in g for g in gaps)


def test_end_to_end_one_chat_and_two_initial_calls():
    class Chat:
        calls = 0
        def parse(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=model_answer()))])
    client = SimpleNamespace(chat=Chat())
    def search(q, limit):
        return [SearchHit(group_id="unit-source", rank=1, group=group())]
    result = run(query(), search, search, None, "owned", client, "unit-model")
    assert result.counters == dict(elastic_logical=2, recovery_logical=0, recovery_rounds=0, mistral_chat=1)
    assert client.chat.calls == 1 and len(result.claims) == 1


def test_truncation_is_explicit():
    bundle, gaps = select_bundle([dict(accepted=True, group=group())], max_chars=8)
    assert len(bundle[0].body) == 8 and bundle[0].coverage == "excerpted" and gaps


def test_raw_scope_substitution_rejected():
    source = group(raw_rows=[dict(row_id="unit-row", fields=dict(camis="00123457",
        inspection_date="2026-01-01T00:00:00", inspection_type="unit-type"))])
    claims, gaps, status = validate_answer(model_answer(), query(), [source])
    assert not claims and any("CLAIM_SCOPE_MISMATCH" in g for g in gaps)


def test_missing_row_provenance_rejected():
    claims, gaps, status = validate_answer(model_answer(), query(), [group(raw_row_ids=[])])
    assert not claims and any("PROVENANCE_MISSING" in g for g in gaps)


def test_missing_first_pass_recovers_exact_group_without_inventing_rank():
    class Elastic:
        def search(self, **kwargs):
            return {"hits": {"total": {"value": 1}, "hits": [{"_source": group().model_dump()}]}}
    r = run(query(), lambda q, limit: [], lambda q, limit: [], Elastic(), "owned")
    assert len(r.evidence) == 1
    assert r.counters["elastic_logical"] == 3 and r.counters["recovery_rounds"] == 1
    assert r.candidates[0]["provenance"] == "exact_recovery"
    assert "lexical_rank" not in r.candidates[0]


def test_malformed_model_uses_source_only_without_repair():
    class Chat:
        calls = 0
        def parse(self, **kwargs):
            self.calls += 1
            raise ValueError("malformed fixture")
    client = SimpleNamespace(chat=Chat())
    def search(q, limit):
        return [SearchHit(group_id="unit-source", rank=1, group=group())]
    r = run(query(), search, search, None, "owned", client, "unit-model")
    assert r.evidence and not r.claims and client.chat.calls == 1
    assert any("ANSWER_FAILED" in gap for gap in r.gaps)


def test_source_wording_inside_quote_is_not_a_completeness_claim():
    body = "- 10F: Equipment not raised to allow cleaning on all sides."
    quote = "Equipment not raised to allow cleaning on all sides."
    claims, gaps, status = validate_answer(model_answer(text=quote, excerpts=[dict(source_id="unit-source", quote=quote)]),
                                           query(), [group(body=body)])
    assert len(claims) == 1 and status == "partial"
    claims, _, _ = validate_answer(model_answer(text=f"All recorded violations: {quote}",
                                                excerpts=[dict(source_id="unit-source", quote=quote)]),
                                   query(), [group(body=body)])
    assert claims == []


def test_validator_feedback_allows_exactly_one_retry_and_records_lessons(tmp_path):
    bad = model_answer(excerpts=[dict(source_id="unit-source", quote="invented quote")])
    good = model_answer()

    class Chat:
        calls, messages = 0, []
        def parse(self, **kwargs):
            Chat.calls += 1
            Chat.messages.append(kwargs["messages"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=bad if Chat.calls == 1 else good, content=None))])
    client = SimpleNamespace(chat=Chat())
    hit = SearchHit(group_id="unit-source", rank=1, score=1.0, group=group())
    ledger = tmp_path / "lessons.json"
    r = run(query(), lambda q, limit: [hit], lambda q, limit: [], None, "unit-index", client, "unit-model",
            {}, lessons_path=ledger)
    assert Chat.calls == 2 and r.counters["mistral_chat"] == 2
    assert len(r.claims) == 1 and [a["attempt"] for a in r.attempts] == [1, 2]
    assert r.attempts[0]["removed_reasons"] == ["QUOTE_MISSING"]
    assert "QUOTE_MISSING" in Chat.messages[1][-1]["content"]
    saved = json.loads(ledger.read_text())
    assert saved["reasons"]["QUOTE_MISSING"] == {"seen": 1, "fixed_by_retry": 1}
    # A second run starts with the code-written lesson in its system prompt; budget still caps at 2.
    Chat.calls, Chat.messages = 0, []
    r2 = run(query(), lambda q, limit: [hit], lambda q, limit: [], None, "unit-index", client, "unit-model",
             {}, lessons_path=ledger)
    assert any("character-for-character" in l for l in r2.lessons_applied)
    assert "Lessons from earlier validator rejections" in Chat.messages[0][0]["content"]
    assert r2.counters["mistral_chat"] <= 2


def test_no_retry_when_first_answer_passes():
    class Chat:
        calls = 0
        def parse(self, **kwargs):
            Chat.calls += 1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=model_answer(), content=None))])
    hit = SearchHit(group_id="unit-source", rank=1, score=1.0, group=group())
    r = run(query(), lambda q, limit: [hit], lambda q, limit: [], None, "unit-index",
            SimpleNamespace(chat=Chat()), "unit-model", {})
    assert Chat.calls == 1 and len(r.attempts) == 1


def test_concurrent_ledger_updates_do_not_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from answer import update_ledger
    path = tmp_path / "lessons.json"
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda i: update_ledger(path, ["QUOTE_MISSING"] if i % 2 else [], None, False), range(40)))
    saved = json.loads(path.read_text())
    assert saved["runs"] == 40 and saved["runs_with_rejections"] == 20
    assert saved["reasons"]["QUOTE_MISSING"]["seen"] == 20
    assert not list(tmp_path.glob("*.tmp"))
