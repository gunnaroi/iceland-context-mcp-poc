from iceland_context_mcp.data_skills import attribution_header, get_data_skill, list_data_skills
from iceland_context_mcp.open_data import open_data_registry_record, open_data_registry_records
from iceland_context_mcp.search import _fts_query
from iceland_context_mcp.sources import (
    _extract_law_basis,
    _regulation_identifier,
    _to_law_citation_tag,
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


def test_data_skills_index_nonempty_and_has_frontmatter():
    skills = list_data_skills()
    assert len(skills) >= 50
    names = {s.name for s in skills}
    assert "althingi" in names
    assert "domstolar" in names
    for s in skills:
        assert s.description, f"{s.name} is missing a description"


def test_data_skill_lookup_and_unknown():
    skill = get_data_skill("althingi")
    assert skill.name == "althingi"
    assert "althingi.is" in skill.body
    try:
        get_data_skill("does-not-exist")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_attribution_header_names_source():
    header = attribution_header("althingi")
    assert "jokull/icelandic-data" in header
    assert "MIT" in header


def test_open_data_registry():
    records = open_data_registry_records()
    keys = {r.key for r in records}
    assert {"umferd", "fiskistofa", "ust-gis", "lmi", "hagstofan", "car", "eurostat", "vedur", "loftgaedi", "lanamal"} <= keys
    lmi = open_data_registry_record("lmi")
    assert "{workspace}" in lmi.base_url
    try:
        open_data_registry_record("does-not-exist")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_law_citation_tag_conversion():
    assert _to_law_citation_tag("91/1991") == "1991.91"
    assert _to_law_citation_tag("90/2018") == "2018.90"
    assert _to_law_citation_tag(" 8 / 1962 ") == "1962.8"


def test_law_citation_tag_rejects_bad_format():
    for bad in ["90-2018", "2018/90", "nr. 90/2018", "90"]:
        try:
            _to_law_citation_tag(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_open_data_registry_new_sources():
    records = open_data_registry_records()
    keys = {r.key for r in records}
    assert {
        "rikisreikningur",
        "opnirreikningar",
        "skipulagsmal",
        "heimsmarkmid",
        "tenders",
        "eea-sdi",
        "natt",
    } <= keys
    natt = open_data_registry_record("natt")
    assert natt.wfs_version == "2.0.0"
    lmi = open_data_registry_record("lmi")
    assert lmi.wfs_version == "2.0.0"
    umferd = open_data_registry_record("umferd")
    assert umferd.wfs_version == "1.0.0"


def test_eur_lex_rejects_unsupported_language():
    from iceland_context_mcp.sources import fetch_eur_lex_act

    async def run():
        await fetch_eur_lex_act("32016R0679", language="is")

    import asyncio

    try:
        asyncio.run(run())
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Unsupported language" in str(e)
