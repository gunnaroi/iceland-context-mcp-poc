from iceland_context_mcp.search import _fts_query
from iceland_context_mcp.sources import normalize_celex, registry_record


def test_celex_normalization():
    assert normalize_celex(" 32016R0679 ") == "32016R0679"


def test_registry():
    src = registry_record("ees_gagnagrunnur")
    assert "EES" in src.name
    assert src.base_url.startswith("https://")


def test_fts_query():
    assert _fts_query("persónuvernd gagna") == '"persónuvernd" AND "gagna"'
