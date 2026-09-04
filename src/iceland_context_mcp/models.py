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


class EurLexActResult(BaseModel):
    celex: str
    title: str | None = None
    document_date: str | None = None
    entry_into_force_date: str | None = None
    end_of_validity_date: str | None = None
    in_force: bool | None = None
    resource_type: str | None = None
    text: str
    provenance: Provenance
    status_note: str = (
        "Official EU act text and metadata from the Publications Office's CELLAR repository (the same backend "
        "eur-lex.europa.eu itself runs on) — metadata via SPARQL, text via content-negotiated REST fetch. "
        "in_force/end_of_validity_date describe EU law only: an EU act being in force says nothing about "
        "Icelandic applicability by itself. Use get_iceland_eea_status/get_efta_eea_factsheet/"
        "trace_eea_public_context for the EEA/Icelandic side of the chain — EU status, EEA incorporation, "
        "and Icelandic implementation/commencement are each separate facts."
    )


class StjornartidindiSearchHit(BaseModel):
    id: str
    department: str
    title: str
    publication_number: str | None = None
    publication_date: str | None = None
    involved_party: str | None = None
    advert_type: str | None = None


class StjornartidindiSearchResult(BaseModel):
    query: str | None = None
    total_items: int
    hits: list[StjornartidindiSearchHit]
    warning: str = (
        "department values are 'a-deild' (laws/presidential acts), 'b-deild' (regulations/administrative "
        "notices — the great majority of volume), or 'c-deild' (international agreements). Use "
        "get_stjornartidindi_advert for full text."
    )


class StjornartidindiAdvertResult(BaseModel):
    id: str
    department: str
    title: str
    publication_number: str | None = None
    publication_date: str | None = None
    signature_date: str | None = None
    involved_party: str | None = None
    advert_type: str | None = None
    categories: list[str] = Field(default_factory=list)
    text: str
    provenance: Provenance
    status_note: str = (
        "Official promulgation text from Stjórnartíðindi (the Icelandic Government Gazette) via island.is. "
        "This is the actual publication event — for a regulation this is often the same text get_regulation "
        "already returns (that tool tracks amendments/consolidation on top; this one is the raw promulgation "
        "record). A-deild carries laws and presidential acts, B-deild regulations and administrative notices "
        "(the bulk of volume), C-deild international agreements."
    )


class RegulationAmendmentEvent(BaseModel):
    date: str | None = None
    official_identifier: str
    title: str
    effect: str
    status: str | None = None


class LawBasisReference(BaseModel):
    law_nr: str
    context: str
    note: str = (
        "Extracted by pattern match on the regulation's own legal-basis clause. "
        "This is best-effort text extraction, not a verified structured field — confirm against the source text."
    )


class RegulationResult(BaseModel):
    official_identifier: str
    title: str
    view: str
    text: str
    ministry: str | None = None
    signature_date: str | None = None
    published_date: str | None = None
    effective_date: str | None = None
    repealed: bool = False
    last_amend_date: str | None = None
    law_chapters: list[str] = Field(default_factory=list)
    history: list[RegulationAmendmentEvent] = Field(default_factory=list)
    effects: list[RegulationAmendmentEvent] = Field(default_factory=list)
    law_basis: list[LawBasisReference] = Field(default_factory=list)
    original_doc_url: str | None = None
    provenance: Provenance
    status_note: str = (
        "Text and metadata from the official reglugerð register. 'current' reflects consolidated text after "
        "published amendments; 'original' is the text as first published in Stjórnartíðindi B-deild. "
        "law_basis is extracted evidence of the enabling statute, not a verified legal determination — a "
        "regulation may also be constrained by provisions beyond the one cited in its own gildistaka clause."
    )


class RegulationSearchHit(BaseModel):
    official_identifier: str
    title: str
    published_date: str | None = None
    ministry: str | None = None


class RegulationSearchResult(BaseModel):
    query: str
    total_items: int
    page: int
    hits: list[RegulationSearchHit]
    warning: str = (
        "Free-text search over the official reglugerð register. Use get_regulation for the authoritative record. "
        "Searching a law citation (e.g. 'nr. 90/2018') surfaces regulations whose text mentions it — a practical "
        "but non-exhaustive way to find regulations issued under a given law; it is not a verified reverse index."
    )


class BillDocument(BaseModel):
    document_number: int
    document_type: str
    distributed_at: str | None = None
    html_url: str | None = None
    pdf_url: str | None = None


class RelatedMatter(BaseModel):
    thing: int
    matter_number: int
    title: str


class CourtRulingSearchHit(BaseModel):
    id: str
    court: str
    case_number: str | None = None
    verdict_date: str | None = None
    title: str
    keywords: list[str] = Field(default_factory=list)
    authority_class: str


class CourtRulingSearchResult(BaseModel):
    query: str | None = None
    total_items: int
    hits: list[CourtRulingSearchHit]
    law_citation: str | None = None
    law_filter_suspicious: bool = False
    warning: str = (
        "Precedential weight differs by court and is reflected in each hit's authority_class "
        "(C1 Hæstiréttur > C2 Landsréttur > C3 héraðsdómur). Use get_court_ruling for full text. "
        "The upstream court filter is confirmed reliable only for court='Hæstiréttur' — passing "
        "'Landsréttur' or a héraðsdómur name silently returns zero results even though those exact "
        "strings appear in the data (an upstream API limitation, verified against the live endpoint, "
        "not a bug in this filter's construction). For other courts, search by query/date and filter "
        "the returned hits' court field client-side instead of relying on the court parameter. "
        "law_citation filters by a curated whole-law citation tag (not full-text search, so it is "
        "sparse/incomplete, not exhaustive) — if law_filter_suspicious is true, total_items came back "
        "large enough that the filter likely fell back to unfiltered rather than genuinely matching; "
        "treat the results as unreliable in that case."
    )


class CourtRulingResult(BaseModel):
    id: str
    court: str
    case_number: str | None = None
    verdict_date: str | None = None
    title: str
    keywords: list[str] = Field(default_factory=list)
    text: str
    text_source: str
    provenance: Provenance
    status_note: str = (
        "Official court ruling from the unified island.is verdict register. Precedential weight differs sharply "
        "by court: Hæstiréttur rulings bind lower courts on questions of law, Landsréttur binds within its "
        "appellate role, and héraðsdómur rulings bind only the parties to that case. A ruling is evidence of how "
        "a law has been applied in one case, not a substitute for the law itself — and text_source='pdf' means "
        "the text below was extracted from a scanned/generated PDF and may contain extraction artifacts."
    )


class BillResult(BaseModel):
    thing: int
    matter_number: int
    matter_class: str
    title: str
    matter_type: str | None = None
    status: str | None = None
    subject_categories: list[str] = Field(default_factory=list)
    rapporteurs: list[str] = Field(default_factory=list)
    related_matters: list[RelatedMatter] = Field(default_factory=list)
    documents: list[BillDocument] = Field(default_factory=list)
    provenance: Provenance
    status_note: str = (
        "Official Alþingi open-XML parliamentary matter record. 'documents' lists the parliamentary paper trail "
        "(e.g. stjórnarfrumvarp/nefndarálit/breytingartillaga). The bill's greinargerð (in the first document, "
        "usually 'stjórnarfrumvarp' or 'þingmannafrumvarp') is the closest public travaux préparatoires evidence "
        "for legislative intent — it is not binding legal text, and only 'status' shows whether the matter became law."
    )


class BillDocumentResult(BaseModel):
    thing: int
    document_number: int
    text: str
    text_source: str
    html_url: str
    pdf_url: str
    provenance: Provenance
    status_note: str = (
        "Full text of one Alþingi þingskjal (document_number, from a BillResult's documents list). Most "
        "documents (nefndarálit, breytingartillaga, smaller frumvörp) render inline HTML and text_source is "
        "'html'; large tabular documents — fjárlög (the state budget) in particular — publish no inline text "
        "('Smellið á PDF...') and this falls back to the document's own PDF, text_source='pdf'. PDF extraction "
        "on table-heavy pages (e.g. fjárlög's appropriation tables) can come out garbled or column-scrambled — "
        "a known pdfplumber limitation with some table layouts, not a data error; treat such pages with care."
    )
