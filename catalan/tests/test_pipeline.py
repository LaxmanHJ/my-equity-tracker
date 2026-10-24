"""The daily pipeline must never lose a day's headlines to a later stage."""
from __future__ import annotations

from datetime import date

from catalan import buy_list, pipeline
from catalan.data import archive, collect_daily, pdf_text
from catalan.data.store import store


def test_a_failing_stage_cannot_lose_the_days_headlines(tmp_path, monkeypatch):
    for name in ("ANNOUNCEMENTS_DIR", "SCORES_DIR", "PDF_TEXT_DIR", "MODEL_CHECK_DIR"):
        monkeypatch.setattr(archive, name, tmp_path / name.lower())
    monkeypatch.setattr(buy_list, "BUY_LIST_DIR", tmp_path / "buy_list")
    monkeypatch.setattr(pipeline, "store", lambda: store(tmp_path / "p.db"))

    def fake_collect(frm, to, conn=None):
        conn.execute(
            "INSERT INTO announcements (symbol, company, announced_at, headline, category, seq_id, "
            "source, ingested_at, attachment_url) VALUES ('TCS', 'TCS', '2026-10-21T18:00:00', "
            "'TCS has informed the Exchange about results', 'Press Release', 's1', 'nse', 'x', "
            "'https://nse.test/a.pdf')")
        conn.commit()
        return {"fetched": 1, "inserted": 1}

    def broken_extract(**kwargs):
        raise RuntimeError("encrypted PDF the extractor did not expect")

    monkeypatch.setattr(collect_daily, "collect", fake_collect)
    monkeypatch.setattr(pdf_text, "extract_pending", broken_extract)

    out = pipeline.run(date(2026, 10, 21), skip_score=True)

    assert out["archive"]["announcements"] == 1, "headlines must be archived before later stages"
    assert "error" in out["pdf_text"], "the failure is recorded, not raised"
    assert (tmp_path / "announcements_dir" / "2026-10-21.ndjson.gz").exists()


def test_the_result_file_stays_valid_json_when_a_pdf_is_broken(tmp_path, monkeypatch):
    """Verifier A1: MuPDF error text used to land inside the parsed result."""
    import json as _json
    import sys
    import fitz

    from catalan.data import pdf_text as _pt
    doc = fitz.open(); doc.new_page().insert_text((72, 72), "x " * 200)
    broken = doc.tobytes()[:300]
    _pt.extract(broken)                     # would print MuPDF errors if not silenced

    result = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["pipeline", "--date", "2026-10-21", "--skip-score",
                                      "--result-file", str(result)])
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: {"date": "2026-10-21", "collect": {"fetched": 1}})
    pipeline.main()
    assert _json.loads(result.read_text())["collect"]["fetched"] == 1


def test_model_check_records_drift_ok_and_idle():
    out = {"score_pdf": {"error": "ModelDrift: requested 'gemini-3.5-flash-lite', Google served 'x'"},
           "score": {"pending": 5, "scored": 5}}
    recs = pipeline._model_checks(out, "gemini-3.5-flash-lite", "2026-10-27T07:00:00")
    assert [(r["arm"], r["status"]) for r in recs] == [("pdf", "drift"), ("headline", "ok")]
    idle = pipeline._model_checks({"score_pdf": {"pending": 0}, "score": {"pending": 3, "quota_deferred": 3}},
                                  "m", "t")
    assert [r["status"] for r in idle] == ["idle", "quota"]


def test_model_checks_are_appended_to_the_archive(tmp_path, monkeypatch):
    import gzip, json as _json
    monkeypatch.setattr(archive, "MODEL_CHECK_DIR", tmp_path / "mc")
    recs = pipeline._model_checks({"score_pdf": {"pending": 2, "scored": 2}, "score": {"pending": 0}},
                                  "gemini-3.5-flash-lite", "2026-10-27T07:00:00")
    assert archive.append_model_checks("2026-10-27", recs) == 2
    assert archive.append_model_checks("2026-10-27", recs) == 0, "idempotent on re-run"
    rows = [_json.loads(l) for l in gzip.open(tmp_path / "mc" / "2026-10-27.ndjson.gz", "rt")]
    assert {r["status"] for r in rows} == {"ok", "idle"}


def test_model_gone_and_operational_errors_are_told_apart():
    recs = pipeline._model_checks({"score_pdf": {"error": "ModelGone: HTTP 404 for gemini-3.5-flash-lite"},
                                   "score": {"error": "UniverseFileChanged: hash mismatch"}}, "m", "t")
    assert [r["status"] for r in recs] == ["model_gone", "error"]


def test_a_drift_after_matching_rows_is_still_an_ok_day():
    """Verifier review 6: matching responses were obtained, so this is not a counting day."""
    recs = pipeline._model_checks({"score_pdf": {"error": "ModelDrift: served x", "scored": 20},
                                   "score": {"error": "ModelDrift: served x", "scored": 0}}, "m", "t")
    assert [r["status"] for r in recs] == ["ok", "drift"]
