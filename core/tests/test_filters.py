from raven_core.search.filters import parse


def test_tokens():
    f = parse("dbms notes type:pdf >2MB after:2026-01 in:Sem5")
    assert f.text == "dbms notes"
    assert f.exts == ["pdf"]
    assert f.min_size == 2 * 1024**2
    assert f.after is not None and f.folder == "Sem5"


def test_phrases():
    f = parse("DBMS notes bigger than 2MB")
    assert f.min_size == 2 * 1024**2 and f.text == "DBMS notes"
    f = parse("photos from last month")
    assert f.exts and f.after and f.before


def test_plain():
    f = parse("rent agreement")
    assert not f.active and f.text == "rent agreement"


def test_age_month_semester_phrases():
    import time as _t

    f = parse("pdfs older than 6 months")
    assert f.before and not f.after and f.text == "pdfs" and f.exts == ["pdf"]
    assert abs(f.before - (_t.time() - 180 * 86400)) < 5
    f = parse("photos in the last 10 days")
    assert f.after and not f.before and f.text == "photos"
    f = parse("invoice in march 2025")
    assert f.text == "invoice" and f.before - f.after == 31 * 86400
    f = parse("dbms notes last semester")
    assert f.text == "dbms notes" and f.after and f.before


def test_number_words_and_ago():
    import time as _t

    f = parse("photos from two weeks ago")
    assert f.after and f.before and f.before - f.after == 14 * 86400
    assert abs(f.before - (_t.time() - 7 * 86400)) < 5
    f = parse("files older than six months")
    assert f.before and not f.after and f.text == "files"
    f = parse("a few days ago notes")
    assert f.text == "notes" and f.after


def test_type_list_token():
    f = parse("dbms type:pdf,docx")
    assert set(f.exts) >= {"pdf", "docx"} and f.text == "dbms"


def test_llm_tokens_drop_bad_values():
    from raven_core.search.nl import tokens_from

    text, toks = tokens_from({"semantic_query": "DBMS notes", "file_types": ["PDF", ".docx", "not a type!"],
                              "modified_after": "2026-01-01", "modified_before": "1700-01-01",
                              "min_size_bytes": 2097152, "max_size_bytes": 0, "folder": "Downloads"})
    assert text == "DBMS notes"
    assert toks == ["type:pdf,docx", "after:2026-01-01", ">2048KB", "in:Downloads"]
    f = parse(" ".join([text, *toks]))
    assert f.min_size == 2 * 1024**2 and f.folder == "Downloads" and "pdf" in f.exts
