"""PDF attachment text — the input to the PDF scoring arm.

What is tested is what decides which rows enter the corpus: the readability
rules, the all-or-nothing fallback, and that a network failure is never stored
as if it were a fact about the document.
"""
from __future__ import annotations

import json
from datetime import datetime

import fitz
import pytest
import requests

from catalan.data import archive, pdf_text
from catalan.data.store import store

BODY = ("Revenues grew 23.5% year-on-year to Rs 2,828 crore and EBITDA margin "
        "expanded 140 basis points, the strongest quarter in two years. ")


def make_pdf(pages):
    """A real PDF; a page given as None is left blank (no text layer)."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


# ── Page classification ──────────────────────────────────────────────────────

def test_a_normal_page_is_text():
    assert pdf_text.classify_page(BODY) == "TEXT"


def test_indian_currency_and_typographic_punctuation_are_not_garble():
    page = "Profit rose to ₹1.63 crore – “a record” • EBITDA up 12%. " * 3
    assert pdf_text.classify_page(page) == "TEXT"


def test_a_bilingual_hindi_letterhead_is_text_not_garble():
    """Measured on RITES, 2026-09-25: Devanagari vowel signs are not letters to
    isalpha(), so an unguarded ratio flagged a normal PSU letter as garbled."""
    page = ("No. RITES/SECY/NSE Date: September 25, 2026 To लिस्टिंग विभाग, नेशनल स्टॉक एक्सचेंज "
            "ऑफ इंडिया लिमिटेड, एक्सचेंज प्लाजा Listing Department, National Stock Exchange "
            "of India Limited, Exchange Plaza, Bandra Kurla Complex, Mumbai 400051. ")
    assert pdf_text.classify_page(page) == "TEXT"


def test_a_near_empty_page_is_a_scan():
    assert pdf_text.classify_page("  Page 3  ") == "NO_TEXT"


def test_a_broken_font_map_is_garbled():
    """Measured on GMRP&UI's 2026 AGM notice, page 2."""
    page = "˦ǼÌŘŘȖĶǊsƼŸǋǼˡ˟ˡˤ˚ˡ˥ ˣ˟˨ µōǊƻŸɠsǋŘ_ȕǋEŘÝŘ¯ǋĵÞŎÞǼs_ ǻŸ OŸŘǣÞ_sǋ Ř_ʰ Þ¯ ǼÌŸȖ¶ÌǼ"
    assert pdf_text.classify_page(page) == "GARBLED"


# ── Extraction ───────────────────────────────────────────────────────────────

def test_reads_only_the_page_window():
    res = pdf_text.extract(make_pdf(["cover letter " + BODY, "p2 " + BODY,
                                     "p3 " + BODY, "PAGE-FOUR-MARKER " + BODY]))
    assert res["status"] == "ok"
    assert res["total_pages"] == 4
    assert res["page_kinds"] == ["TEXT"] * pdf_text.PDF_PAGE_WINDOW
    assert "[page 3]" in res["text"] and "PAGE-FOUR-MARKER" not in res["text"]


def test_a_short_filing_is_read_whole():
    res = pdf_text.extract(make_pdf([BODY]))
    assert res["status"] == "ok" and res["page_kinds"] == ["TEXT"]


def test_one_unreadable_page_in_the_window_sends_the_filing_to_headline_only():
    """All-or-nothing: partial text would be a third, undeclared instrument."""
    res = pdf_text.extract(make_pdf([BODY, None, BODY]))
    assert res["status"] == "unreadable"
    assert res["page_kinds"] == ["TEXT", "NO_TEXT", "TEXT"]
    assert res["text"] is None


def test_an_unreadable_page_outside_the_window_does_not_matter():
    res = pdf_text.extract(make_pdf([BODY, BODY, BODY, None]))
    assert res["status"] == "ok"


@pytest.mark.parametrize("payload", [b"<html>404</html>", b"PK\x03\x04zip", b"%PDF-1.7 garbage"])
def test_non_pdf_attachments_are_recorded_as_such(payload):
    res = pdf_text.extract(payload)
    assert res["status"] == "not_pdf" and res["text"] is None
    assert res["sha256"]


def test_over_long_text_is_truncated_and_flagged(monkeypatch):
    monkeypatch.setattr(pdf_text, "PDF_MAX_CHARS", 100)
    res = pdf_text.extract(make_pdf([BODY * 3]))
    assert res["status"] == "ok" and res["truncated"] == 1
    assert len(res["text"]) == 100


def test_extraction_is_deterministic():
    data = make_pdf([BODY, "second " + BODY])
    assert pdf_text.extract(data) == pdf_text.extract(data)


# ── The pending loop ─────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, content, status=200, content_type="application/pdf"):
        self.content, self.status_code, self.ok = content, status, status < 400
        self.headers = {"content-type": content_type}


class FakeSession:
    def __init__(self, by_url):
        self.by_url, self.calls = by_url, []

    def get(self, url, timeout):
        self.calls.append(url)
        hit = self.by_url[url]
        if isinstance(hit, Exception):
            raise hit
        return hit


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_text, "REQUEST_PAUSE_SECONDS", 0)
    conn = store(tmp_path / "pdf.db")
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        ("WELSPUNLIV", "Outcome of Board Meeting", "https://nse.test/ok.pdf", "s1"),
        ("GICL", "Outcome of Board Meeting", "https://nse.test/scan.pdf", "s2"),
        ("TCS", "Press Release", "https://nse.test/down.pdf", "s3"),
        ("INFY", "Copy of Newspaper Publication", "https://nse.test/paper.pdf", "s4"),
        ("SBIN", "Updates", None, "s5"),
    ]
    conn.executemany(
        "INSERT INTO announcements (symbol, company, announced_at, headline, category, "
        "seq_id, source, ingested_at, attachment_url) VALUES (?, ?, '2026-09-25T18:00:00', "
        "'headline', ?, ?, 'test', ?, ?)",
        [(s, s, c, q, now, u) for s, c, u, q in rows],
    )
    conn.commit()
    yield conn
    conn.close()


def _session():
    return FakeSession({
        "https://nse.test/ok.pdf": FakeResponse(make_pdf([BODY, BODY])),
        "https://nse.test/scan.pdf": FakeResponse(make_pdf([None])),
        "https://nse.test/down.pdf": requests.ConnectionError("reset"),
    })


def test_pending_skips_excluded_categories_and_rows_without_attachments(db):
    assert {r["symbol"] for r in pdf_text.pending(db)} == {"WELSPUNLIV", "GICL", "TCS"}


def test_a_network_failure_is_never_stored_and_stays_pending(db):
    counts = pdf_text.extract_pending(conn=db, session=_session())
    assert counts["ok"] == 1 and counts["unreadable"] == 1 and counts["fetch_failed"] == 1
    stored = {r[0] for r in db.execute(
        "SELECT a.symbol FROM pdf_texts p JOIN announcements a ON a.id = p.announcement_id")}
    assert stored == {"WELSPUNLIV", "GICL"}
    assert [r["symbol"] for r in pdf_text.pending(db)] == ["TCS"]


def test_a_rerun_only_touches_what_is_still_pending(db):
    pdf_text.extract_pending(conn=db, session=_session())
    again = _session()
    pdf_text.extract_pending(conn=db, session=again)
    assert again.calls == ["https://nse.test/down.pdf"]


def test_dry_run_downloads_nothing(db):
    s = _session()
    counts = pdf_text.extract_pending(conn=db, session=s, dry_run=True)
    assert counts["would_extract"] == 3 and s.calls == []


# ── Archive round-trip ───────────────────────────────────────────────────────

def test_pdf_text_survives_export_and_rebuild(db, tmp_path, monkeypatch):
    for name in ("ANNOUNCEMENTS_DIR", "SCORES_DIR", "PDF_TEXT_DIR"):
        monkeypatch.setattr(archive, name, tmp_path / name.lower())
    pdf_text.extract_pending(conn=db, session=_session())
    day = db.execute("SELECT substr(max(extracted_at), 1, 10) FROM pdf_texts").fetchone()[0]
    archive.export_announcements("2026-09-25", conn=db)
    assert archive.export_pdf_texts(day, conn=db) == 2
    assert archive.export_pdf_texts(day, conn=db) == 0   # idempotent

    fresh = store(tmp_path / "rebuilt.db")
    archive.rebuild(conn=fresh)
    cols = "a.symbol, p.status, p.page_kinds, p.text, p.sha256"
    q = (f"SELECT {cols} FROM pdf_texts p JOIN announcements a "
         "ON a.id = p.announcement_id ORDER BY a.symbol")
    assert fresh.execute(q).fetchall() == db.execute(q).fetchall()
    kinds = fresh.execute("SELECT page_kinds FROM pdf_texts p JOIN announcements a "
                          "ON a.id = p.announcement_id WHERE a.symbol='GICL'").fetchone()[0]
    assert json.loads(kinds) == ["NO_TEXT"]
    fresh.close()


# ── ZIP attachments and the study window (2026-09-29) ────────────────────────

import io
import zipfile


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def test_a_zip_is_read_through_its_first_pdf_by_name():
    """NSE files director/KMP resignations as .zip: an XML plus the letter."""
    data = _zip([("b_letter.pdf", make_pdf(["SECOND " + BODY])),
                 ("a_letter.pdf", make_pdf(["FIRST " + BODY])),
                 ("filing.xml", b"<xml/>")])
    res = pdf_text.extract(data)
    assert res["status"] == "ok" and "FIRST" in res["text"] and "SECOND" not in res["text"]


def test_a_zip_without_a_pdf_is_not_pdf():
    assert pdf_text.extract(_zip([("filing.xml", b"<xml/>")]))["status"] == "not_pdf"


def test_a_corrupt_zip_is_not_pdf():
    assert pdf_text.extract(b"PK\x03\x04garbage")["status"] == "not_pdf"


def test_since_limits_extraction_to_the_study_window(db):
    db.execute("UPDATE announcements SET announced_at = '2026-10-01T18:00:00' WHERE symbol = 'TCS'")
    db.commit()
    assert [r["symbol"] for r in pdf_text.pending(db, since="2026-10-01")] == ["TCS"]


# ── Verifier review 1 (2026-10-18): a bad document must never stop the run ────

def test_a_password_protected_pdf_is_unreadable_not_a_crash():
    """B1: opening succeeded, reading pages raised 'document closed or encrypted'."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), BODY)
    data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    doc.close()
    assert pdf_text.extract(data)["status"] == "unreadable"


def test_a_zip_with_corrupt_compressed_data_is_not_pdf_not_a_crash():
    """B1: corrupt deflate data raises zlib.error, which BadZipFile does not cover."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("letter.pdf", make_pdf([BODY * 5]))
    raw = bytearray(buf.getvalue())
    for i in range(60, 400):          # inside the compressed member data
        raw[i] ^= 0xFF
    assert pdf_text.extract(bytes(raw))["status"] == "not_pdf"


def test_junk_before_the_pdf_header_is_tolerated():
    assert pdf_text.extract(b"\r\n\xef\xbb\xbf" + make_pdf([BODY]))["status"] == "ok"


def test_an_html_error_page_served_as_200_is_retried_not_stored():
    s = FakeSession({"https://nse.test/x.pdf": FakeResponse(b"<html>oops</html>",
                                                             content_type="text/html; charset=utf-8")})
    assert pdf_text.fetch(s, "https://nse.test/x.pdf") is None


def test_the_dash_placeholder_url_is_never_fetched(db):
    """S2: NSE's '-' (exchange queries) is not a URL; it used to be retried daily."""
    db.execute("UPDATE announcements SET attachment_url = '-' WHERE symbol = 'TCS'")
    db.commit()
    assert "TCS" not in {r["symbol"] for r in pdf_text.pending(db)}


def test_an_extractor_crash_is_counted_and_skipped(db, monkeypatch):
    def boom(_):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(pdf_text, "extract", boom)
    counts = pdf_text.extract_pending(conn=db, session=_session())
    assert counts["extract_error"] == 2 and counts["ok"] == 0
    assert db.execute("SELECT COUNT(*) FROM pdf_texts").fetchone()[0] == 0
