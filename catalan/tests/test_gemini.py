"""The Gemini transport (catalan-v2 primary model), against a fake HTTP layer.

What matters is what reaches the store: a silently swapped model must never
write a row, a spent quota must defer (not fail) the rest, and a run that stops
early must keep everything scored before the stop.
"""
from __future__ import annotations

import json

import pytest

from catalan.data.store import store
from catalan.scoring import scorer

MODEL = "gemini-3.5-flash-lite"


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self.ok = status, status < 400
        self._payload = payload or {}
        self.text = json.dumps(self._payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload


def ok(text="YES\nGood order win.", served=MODEL):
    return Resp(200, {"modelVersion": served,
                      "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(scorer, "GEMINI_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(scorer.time, "sleep", lambda s: None)


def queue(monkeypatch, responses):
    calls = []

    def post(url, headers, json, timeout):
        calls.append({"url": url, "json": json})
        return responses.pop(0)
    import requests
    monkeypatch.setattr(requests, "post", post)
    return calls


@pytest.fixture
def db(tmp_path):
    conn = store(tmp_path / "g.db")
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO announcements (id, symbol, company, announced_at, headline, category, "
            "seq_id, source, ingested_at) VALUES (?, 'TCS', 'TCS', ?, ?, 'Press Release', ?, 'nse', 'x')",
            (i, f"2026-10-2{i}T18:00:00", f"TCS filing number {i}", f"s{i}"))
    conn.commit()
    yield conn
    conn.close()


def test_temperature_zero_goes_only_to_the_pinned_model(monkeypatch):
    calls = queue(monkeypatch, [ok(), ok(served="gemini-3.8-flash")])
    scorer._score_one_sync("TCS", "x", MODEL)
    scorer._score_one_sync("TCS", "x", "gemini-3.8-flash")
    assert calls[0]["json"]["generationConfig"]["temperature"] == 0.0
    assert "temperature" not in calls[1]["json"]["generationConfig"]
    assert calls[0]["url"].endswith(f"/models/{MODEL}:generateContent")


def test_a_swapped_model_raises_and_writes_nothing_after_it(db, monkeypatch):
    """Google rerouted gemini-3.5-flash to 3.6-flash on 2026-10-08."""
    queue(monkeypatch, [ok(), ok(), ok(served="gemini-3.6-flash-lite")])
    with pytest.raises(scorer.ModelDrift):
        scorer.score_pending(MODEL, conn=db)
    rows = db.execute("SELECT announcement_id FROM scores ORDER BY 1").fetchall()
    assert len(rows) == 2, "rows scored before the drift are kept; none after"


def test_a_missing_model_version_is_treated_as_drift(monkeypatch):
    payload = {"candidates": [{"content": {"parts": [{"text": "YES"}]}}]}
    queue(monkeypatch, [Resp(200, payload)])
    with pytest.raises(scorer.ModelDrift):
        scorer._score_one_sync("TCS", "x", MODEL)


def test_a_spent_daily_quota_defers_the_rest_without_failing_them(db, monkeypatch):
    per_day = Resp(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota",
                                    "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
    queue(monkeypatch, [ok(), ok(), per_day])
    out = scorer.score_pending(MODEL, conn=db)
    assert out["scored"] == 2 and out["quota_deferred"] == 3 and out["api_failed"] == 0
    assert len(scorer.pending(MODEL, conn=db)) == 3, "deferred rows stay pending for tomorrow"


def test_a_per_minute_limit_is_waited_out_and_retried(monkeypatch):
    per_minute = Resp(429, {"error": {"message": "rate", "details": [{"retryDelay": "7s"}]}})
    calls = queue(monkeypatch, [per_minute, ok()])
    score, _, failed = scorer._score_one_sync("TCS", "x", MODEL)
    assert (score, failed, len(calls)) == (1, False, 2)


def _empty(finish):
    return Resp(200, {"modelVersion": MODEL,
                      "candidates": [{"content": {"parts": []}, "finishReason": finish}]})


def test_a_truncated_empty_answer_stays_pending(monkeypatch):
    """Verifier G1: emptiness from truncation says nothing fixed about the input.
    Not retried within the run, so a stubborn input cannot drain the quota."""
    calls = queue(monkeypatch, [_empty("MAX_TOKENS")])
    assert scorer._score_one_sync("TCS", "x", MODEL) == (None, None, True)
    assert len(calls) == 1


def test_a_safety_refusal_is_recorded_once_as_an_abstention(monkeypatch):
    queue(monkeypatch, [_empty("SAFETY")])
    score, rationale, failed = scorer._score_one_sync("TCS", "x", MODEL)
    assert score is None and not failed and "finishReason=SAFETY" in rationale


def test_thinking_tokens_under_the_same_name_are_drift(monkeypatch):
    """No thinking tokens is a declared property; thinking switched on silently is a new model."""
    r = ok()
    r._payload["usageMetadata"] = {"thoughtsTokenCount": 120}
    queue(monkeypatch, [r])
    with pytest.raises(scorer.ModelDrift):
        scorer._score_one_sync("TCS", "x", MODEL)


def test_a_non_json_body_fails_one_call_not_the_run(db, monkeypatch):
    """Verifier G2: it used to escape score_pending and lose the chunk."""
    junk = Resp(200)
    junk.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    queue(monkeypatch, [ok(), ok()] + [junk] * scorer.MAX_RETRIES + [ok(), ok()])
    out = scorer.score_pending(MODEL, conn=db)
    assert out["scored"] == 4 and out["api_failed"] == 1


def test_a_blocked_prompt_without_model_version_is_not_drift(monkeypatch):
    queue(monkeypatch, [Resp(200, {"promptFeedback": {"blockReason": "SAFETY"}})])
    score, rationale, failed = scorer._score_one_sync("TCS", "x", MODEL)
    assert score is None and not failed and "SAFETY" in rationale, \
        "the placeholder text must never parse as a verdict"


@pytest.mark.parametrize("finish", sorted(scorer.FIXED_REFUSALS))
def test_no_refusal_placeholder_parses_as_a_verdict(finish):
    assert scorer.parse_response(f"[empty answer: finishReason={finish}]")[0] is None
    assert scorer.parse_response(f"[prompt blocked: {finish}]")[0] is None


def test_a_missing_key_fails_the_call_not_the_run(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    assert scorer._score_one_sync("TCS", "x", MODEL) == (None, None, True)


def test_gemini_refuses_the_batch_transport(db):
    with pytest.raises(ValueError):
        scorer.score_pending(MODEL, transport=scorer.TRANSPORT_BATCH, conn=db)


def test_the_universe_filter_keeps_only_declared_symbols(db):
    db.execute("INSERT INTO announcements (id, symbol, company, announced_at, headline, category, "
               "seq_id, source, ingested_at) VALUES (9, 'TINYCO', 'TINYCO', '2026-10-21T18:00:00', "
               "'TINYCO news', 'Press Release', 's9', 'nse', 'x')")
    db.commit()
    assert 9 in {r["id"] for r in scorer.pending(MODEL, conn=db)}
    assert 9 not in {r["id"] for r in scorer.pending(MODEL, conn=db, universe=True)}


def test_no_candidates_without_a_reason_stays_pending_and_is_not_a_verdict(monkeypatch):
    """Verifier review 4: '[prompt blocked: no candidates]' used to parse as NO."""
    calls = queue(monkeypatch, [Resp(200, {"modelVersion": MODEL, "candidates": []})])
    assert scorer._score_one_sync("TCS", "x", MODEL) == (None, None, True)
    assert len(calls) == 1, "unexplained emptiness is not retried within a run"


def test_a_refusal_placeholder_is_stored_without_parsing(monkeypatch):
    """Even a reason word that is itself a verdict word must not become one."""
    queue(monkeypatch, [Resp(200, {"promptFeedback": {"blockReason": "NO_UNKNOWN_YES"}})])
    score, rationale, failed = scorer._score_one_sync("TCS", "x", MODEL)
    assert score is None and not failed and rationale.startswith("[prompt blocked:")


def test_reparse_never_turns_a_placeholder_into_a_verdict(db):
    db.execute("INSERT INTO scores (announcement_id, model_id, prompt_hash, score, rationale, "
               "run_id, scored_at, temperature) VALUES (1, ?, ?, NULL, '[prompt blocked: no candidates]', "
               "'r', 'x', 0.0)", (MODEL, scorer.arm_hash(scorer.ARM_HEADLINE)))
    db.commit()
    assert scorer.reparse_stored(MODEL, conn=db)["recovered"] == 0
    assert db.execute("SELECT score FROM scores").fetchone()[0] is None


def test_a_404_for_the_model_is_model_gone_and_halts(db, monkeypatch):
    queue(monkeypatch, [ok(), Resp(404, {"error": {"message": "model not found"}})])
    with pytest.raises(scorer.ModelGone):
        scorer.score_pending(MODEL, conn=db)
    assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 1
