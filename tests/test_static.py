from iceland_context_mcp.search import _fts_query
from iceland_context_mcp.sources import (
    _extract_law_basis,
    _regulation_identifier,
    normalize_celex,
    registry_record,
)


def test_celex_normalization():
    assert normalize_celex(" 32016R0679 ") == "32016R0679"


def test_registry():
    src = registry_record("ees_gagnagrunnur")
    assert "EES" in src.name
    assert src.base_url.startswith("https://")


def test_fts_query():
    assert _fts_query("persónuvernd gagna") == '"persónuvernd" AND "gagna"'


def test_regulation_identifier():
    assert _regulation_identifier(615, 2026) == "0615-2026"
    assert _regulation_identifier(90, 2018) == "0090-2018"


def test_law_basis_extraction_adjacent_form():
    text = (
        "<p>Reglugerð þessi er sett samkvæmt heimild í ákvæði k-liðar 1. mgr. 23. gr. "
        "laga nr. 136/2022 um landamæri og öðlast hún gildi 12. október 2025.</p>"
    )
    refs = _extract_law_basis(text)
    assert [r.law_nr for r in refs] == ["136/2022"]


def test_law_basis_extraction_law_name_between():
    text = (
        "<p>Reglugerð þessi er sett með heimild í 20. gr. laga um sviðslistir "
        "nr. 165/2019 og öðlast þegar gildi.</p>"
    )
    refs = _extract_law_basis(text)
    assert [r.law_nr for r in refs] == ["165/2019"]


def test_law_basis_extraction_no_match():
    assert _extract_law_basis("<p>Reglugerð þessi öðlast þegar gildi.</p>") == []
