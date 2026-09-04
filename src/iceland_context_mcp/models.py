from __future__ import annotations

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    publisher: str
    source_url: str
    retrieved_at: str
    authority_class: str
    authority_label: str
    representation: str = "public official web publication"
    warning: str | None = None


class SourceRecord(BaseModel):
    key: str
    name: str
    publisher: str
    authority_class: str
    authority_label: str
    base_url: str
    machine_source: str | None = None
    notes: str


class SourceRegistryResult(BaseModel):
    sources: list[SourceRecord]


class LawResult(BaseModel):
    official_identifier: str
    title: str | None = None
    text: str
    provenance: Provenance
    status_note: str = Field(
        default="Consolidated Lagasafn text returned from the current official page; verify promulgated text/commencement where legally material."
    )


class SearchHit(BaseModel):
    official_identifier: str | None = None
    title: str
    snippet: str
    source_url: str | None = None
    index_snapshot: str | None = None


class LawSearchResult(BaseModel):
    query: str
    hits: list[SearchHit]
    warning: str = (
        "This is a discovery index built from an Alþingi Lagasafn snapshot. "
        "Use get_law for the live current consolidated page before relying on text."
    )


class EeaField(BaseModel):
    label: str
    value: str


class EeaResult(BaseModel):
    celex: str
    title: str | None = None
    fields: list[EeaField]
    extracted_text: str
    provenance: Provenance
    legal_status_rule: str = (
        "EU status, EEA incorporation/entry into force, Icelandic implementation, and domestic commencement are distinct. "
        "Do not infer Icelandic applicability merely from the existence of an EU act."
    )


class EeaCombinedResult(BaseModel):
    celex: str
    iceland: EeaResult
    efta: EeaResult | None = None
    interpretation_note: str = (
        "Treat the sources as evidence about different stages of the provenance chain. "
        "Any final claim about Icelandic applicability should identify the relevant EEA and domestic implementation/commencement evidence."
    )
