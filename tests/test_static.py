from iceland_context_mcp.data_skills import attribution_header, get_data_skill, list_data_skills
from iceland_context_mcp.open_data import (
    _decode_hagstofa_csv,
    _decode_service_id,
    _richtext_to_markdown,
    open_data_registry_record,
    open_data_registry_records,
)
from iceland_context_mcp.search import _fts_query
from iceland_context_mcp.sources import (
    _clean_text,
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


def test_clean_text_collapses_word_per_line_source_markup():
    from bs4 import BeautifulSoup

    # Reproduces a real pattern seen live in legacy Stjórnartíðindi HTML: literal
    # newlines between every word inside a single text node, not separate tags.
    html = "<p>\nÁbyrgðaraðili\nskal\ní\nákveðnum\ntilfellum\n</p><p>\nAnnar\nkafli.\n</p>"
    soup = BeautifulSoup(html, "lxml")
    text = _clean_text(soup)
    assert text == "Ábyrgðaraðili skal í ákveðnum tilfellum\nAnnar kafli."


def test_clean_text_normal_html_unaffected():
    from bs4 import BeautifulSoup

    html = "<h1>Title</h1><p>First paragraph.</p><p>Second paragraph.</p>"
    soup = BeautifulSoup(html, "lxml")
    text = _clean_text(soup)
    assert text == "Title\nFirst paragraph.\nSecond paragraph."


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


def test_hagstofa_bad_path_raises_actionable_tool_error():
    from mcp.server.mcpserver.exceptions import ToolError

    from iceland_context_mcp.open_data import get_hagstofa_table

    async def run():
        await get_hagstofa_table("THJ01103.px")

    import asyncio

    try:
        asyncio.run(run())
        assert False, "expected ToolError"
    except ToolError as e:
        assert "browse_hagstofa_tables" in str(e)


def test_decode_hagstofa_csv_trusts_bom_over_mislabeled_declared_encoding():
    # Some Hagstofa PX-Web tables send a genuine UTF-8 (with BOM) body while declaring
    # charset=Windows-1252 in Content-Type — decoding with the declared charset raises
    # UnicodeDecodeError on Icelandic characters. The BOM must win.
    content = "Mánuður,Vísitala".encode("utf-8-sig")
    assert _decode_hagstofa_csv(content, "windows-1252") == "Mánuður,Vísitala"


def test_decode_hagstofa_csv_uses_declared_encoding_without_bom():
    content = "Mánuður,Vísitala".encode("windows-1252")
    assert _decode_hagstofa_csv(content, "windows-1252") == "Mánuður,Vísitala"


def test_decode_service_id_roundtrip():
    # "Landlæknir API" — confirmed live in island-is-mcp's discovery-notes.md before this was ported.
    identity = _decode_service_id(
        "SVNfR09WXzcxMDE2OTUwMDlfRW1iYWV0dGlMYW5kbGFla25pcy1Qcm90ZWN0ZWRfbGFuZGxhZWtuaXI"
    )
    assert identity is not None
    assert identity.instance == "IS"
    assert identity.member_class == "GOV"
    assert identity.member_code == "7101695009"
    assert identity.subsystem_code == "EmbaettiLandlaeknis-Protected"
    assert identity.service_code == "landlaeknir"


def test_decode_service_id_handles_underscore_in_service_code():
    # "Bank Info" (FJS-Public/TBRInfo_v1) — serviceCode itself contains an underscore.
    identity = _decode_service_id("SVNfR09WXzU0MDI2OTc1MDlfRkpTLVB1YmxpY19UQlJJbmZvX3Yx")
    assert identity is not None
    assert identity.subsystem_code == "FJS-Public"
    assert identity.service_code == "TBRInfo_v1"


def test_decode_service_id_invalid_input_returns_none():
    assert _decode_service_id("not-valid-base64!!!") is None


def test_richtext_to_markdown_bold_and_paragraphs():
    doc = {
        "nodeType": "document",
        "content": [
            {
                "nodeType": "paragraph",
                "content": [
                    {"nodeType": "text", "value": "Venjulegt ", "marks": []},
                    {"nodeType": "text", "value": "feitletrað", "marks": [{"type": "bold"}]},
                ],
            }
        ],
    }
    assert _richtext_to_markdown(doc).strip() == "Venjulegt **feitletrað**"


def test_richtext_to_markdown_lists_and_links():
    doc = {
        "nodeType": "document",
        "content": [
            {
                "nodeType": "unordered-list",
                "content": [
                    {
                        "nodeType": "list-item",
                        "content": [
                            {
                                "nodeType": "hyperlink",
                                "data": {"uri": "https://example.is"},
                                "content": [{"nodeType": "text", "value": "tengill", "marks": []}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    assert "[tengill](https://example.is)" in _richtext_to_markdown(doc)


def test_open_data_registry_island_sources():
    records = open_data_registry_records()
    keys = {r.key for r in records}
    assert {"island-catalogue", "island-content"} <= keys
    catalogue = open_data_registry_record("island-catalogue")
    assert catalogue.base_url == "https://island.is/api/graphql"


def test_search_webservices_live():
    from iceland_context_mcp.open_data import search_webservices

    async def run():
        return await search_webservices("landlæknir")

    import asyncio

    result = asyncio.run(run())
    assert result.returned >= 1
    assert any("Landlæknir" in s.title for s in result.services)


def test_get_island_article_live():
    from iceland_context_mcp.open_data import get_island_article

    async def run():
        return await get_island_article("saekja-um-vegabref")

    import asyncio

    article = asyncio.run(run())
    assert article.title == "Sækja um vegabréf"
    assert len(article.body_markdown) > 100


# --- Hagstofa catalogue search / table filters ------------------------------------------------


def test_hagstofa_catalog_seed_is_complete_and_searchable():
    from iceland_context_mcp import hagstofa_catalog as catalog

    snapshot = catalog.load_snapshot()
    assert snapshot is not None and len(snapshot.tables) > 1500
    for query in ("inflation", "verðbólga", "vísitala neysluverðs"):
        paths = [h.path for h in catalog.search_catalog(snapshot, query, 5)]
        assert "Efnahagur/visitolur/1_vnv/1_vnv/VIS01000.px" in paths, query


def test_hagstofa_catalog_search_folds_accents_and_inflection():
    from iceland_context_mcp import hagstofa_catalog as catalog

    assert catalog.fold("Þjóðhagsreikningar") == "thjodhagsreikningar"
    snapshot = catalog.load_snapshot()
    # ASCII-typed and inflected forms must find the same table as the exact Icelandic title words
    assert catalog.search_catalog(snapshot, "landsframleidslu", 3)
    assert catalog.search_catalog(snapshot, "launavisitala", 3)


def test_hagstofa_catalog_search_no_match_returns_empty():
    from iceland_context_mcp import hagstofa_catalog as catalog

    assert catalog.search_catalog(catalog.load_snapshot(), "zzzzqqqq", 5) == []


def test_hagstofa_filter_values_accept_codes_labels_and_wildcard():
    from mcp.server.mcpserver.exceptions import ToolError

    from iceland_context_mcp.open_data import _resolve_filter_values

    variable = {"code": "Vísitala", "values": ["CPI", "CPILH"], "valueTexts": ["Vísitala neysluverðs", "Án húsnæðis"]}
    assert _resolve_filter_values(variable, ["CPI"], "t") == ["CPI"]
    assert _resolve_filter_values(variable, ["án húsnæðis"], "t") == ["CPILH"]
    assert _resolve_filter_values(variable, ["CPI", "*"], "t") == ["*"]
    try:
        _resolve_filter_values(variable, ["NOPE"], "t")
        assert False, "expected ToolError"
    except ToolError as e:
        assert "get_hagstofa_table_info" in str(e)


# --- Eurostat discovery -----------------------------------------------------------------------

TOC_SAMPLE = (
    '"title"\t"code"\t"type"\t"last update of data"\t"last table structure change"\t"data start"\t"data end"\t"values"\n'
    '"Database by themes"\t"data"\t"folder"\t" "\t" "\t" "\t" "\t\n'
    '"    Economy and finance"\t"economy"\t"folder"\t" "\t" "\t" "\t" "\t\n'
    '"        Prices"\t"prc"\t"folder"\t" "\t" "\t" "\t" "\t\n'
    '"            HICP - monthly data (index)"\t"prc_hicp_midx"\t"dataset"\t"06.02.2026"\t"07.01.2026"\t"1996-01"\t"2025-12"\t7699058\n'
    '"        National accounts"\t"na"\t"folder"\t" "\t" "\t" "\t" "\t\n'
    '"            GDP and main components"\t"nama_10_gdp"\t"dataset"\t"29.09.2026"\t"08.09.2026"\t"1975"\t"2025"\t1095720\n'
)


def test_eurostat_toc_parse_and_search():
    from iceland_context_mcp.eurostat import _parse_toc, search_toc

    entries = _parse_toc(TOC_SAMPLE)
    assert [e.code for e in entries] == ["prc_hicp_midx", "nama_10_gdp"]
    assert entries[0].theme == "Economy and finance > Prices"
    assert entries[0].observation_count == 7699058 and entries[0].data_end == "2025-12"
    assert search_toc(entries, "inflation", 5)[0].code == "prc_hicp_midx"  # synonym for hicp
    assert search_toc(entries, "nama_10_gdp", 5)[0].code == "nama_10_gdp"
    assert search_toc(entries, "zzz", 5) == []


STRUCTURE_SAMPLE = b"""<?xml version="1.0"?>
<m:Structure xmlns:m="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
  xmlns:s="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
  xmlns:c="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"
  xmlns:xml="http://www.w3.org/XML/1998/namespace"><m:Structures>
<s:Dataflows><s:Dataflow id="X"><c:Name xml:lang="de">HVPI</c:Name><c:Name xml:lang="en">HICP</c:Name></s:Dataflow></s:Dataflows>
<s:Codelists><s:Codelist id="GEO"><s:Code id="IS"><c:Name xml:lang="de">Island</c:Name><c:Name xml:lang="en">Iceland</c:Name></s:Code>
<s:Code id="DE"><c:Name xml:lang="en">Germany</c:Name></s:Code></s:Codelist></s:Codelists>
<s:DataStructures><s:DataStructure id="X"><s:DataStructureComponents><s:DimensionList>
<s:Dimension id="geo" position="1"><s:LocalRepresentation><s:Enumeration><Ref id="GEO"/></s:Enumeration></s:LocalRepresentation></s:Dimension>
<s:TimeDimension id="TIME_PERIOD" position="2"/></s:DimensionList></s:DataStructureComponents></s:DataStructure></s:DataStructures>
</m:Structures></m:Structure>"""


def test_eurostat_structure_parse_prefers_english_and_validates_filters():
    from mcp.server.mcpserver.exceptions import ToolError

    from iceland_context_mcp.eurostat import parse_structure, validate_filters

    structure = parse_structure("x", STRUCTURE_SAMPLE)
    assert structure.title == "HICP"
    geo, time = structure.dimensions
    assert geo.codes == {"IS": "Iceland", "DE": "Germany"} and time.is_time
    validate_filters(structure, {"geo": "IS+DE"})
    for bad in ({"geo": "ZZ"}, {"nope": "IS"}):
        try:
            validate_filters(structure, bad)
            assert False, "expected ToolError"
        except ToolError:
            pass


def test_eurostat_jsonstat_decode_last_dimension_varies_fastest_with_flags_and_gaps():
    from iceland_context_mcp.open_data import decode_eurostat_jsonstat

    # geo (DE, IS) x time (2025-11, 2025-12): flat index = geo_pos * 2 + time_pos.
    data = {
        "id": ["geo", "time"],
        "size": [2, 2],
        "dimension": {
            "geo": {"category": {"index": {"DE": 0, "IS": 1}, "label": {"DE": "Germany", "IS": "Iceland"}}},
            "time": {"category": {"index": {"2025-11": 0, "2025-12": 1}, "label": {"2025-11": "2025-11", "2025-12": "2025-12"}}},
        },
        "value": {"0": 132.6, "1": 132.8, "3": 133.36},
        "status": {"1": "p", "2": ":"},
    }
    observations, total = decode_eurostat_jsonstat(data)
    got = {(o.dimensions["geo"], o.dimensions["time"]): (o.value, o.flag) for o in observations}
    assert total == 4
    assert got[("Germany", "2025-12")] == (132.8, "p")
    assert got[("Iceland", "2025-11")] == (None, ":")  # flagged as not available, no value
    assert got[("Iceland", "2025-12")] == (133.36, None)
