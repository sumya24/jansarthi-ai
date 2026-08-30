"""Schema for the RAG civic-knowledge-base data set (data/rag_knowledge_base/).

This is data-preparation only: these models validate and shape a structured, source-cited
knowledge base of civic-service information (waste/sanitation, water/drainage, roads/potholes,
streetlights), NOT an embedding pipeline, vector store, or retrieval endpoint — none of that
exists yet. When it does, it should import these exact classes rather than redefining them.

The one rule every model here exists to enforce: a record is either genuinely traceable to a
real, currently-live government source (VERIFIED) or explicitly and unmissably marked
SYNTHETIC — never blended, never a fabricated URL/department/SLA presented as verified. See
KnowledgeRecord's model_validator for the automated version of that rule.
"""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.config import settings


class SourceType(str, Enum):
    """What kind of official artifact (or lack thereof) a record's information comes from."""

    OFFICIAL_GOVERNMENT_WEBPAGE = "OFFICIAL_GOVERNMENT_WEBPAGE"
    OFFICIAL_GOVERNMENT_PDF = "OFFICIAL_GOVERNMENT_PDF"
    OFFICIAL_OGD_DATASET = "OFFICIAL_OGD_DATASET"
    OFFICIAL_GOVERNMENT_API = "OFFICIAL_GOVERNMENT_API"
    SYNTHETIC_REPRESENTATIVE = "SYNTHETIC_REPRESENTATIVE"


class VerificationStatus(str, Enum):
    """Whether a record has been individually checked against a real, live source."""

    VERIFIED = "VERIFIED"
    SYNTHETIC = "SYNTHETIC"


class GeographicScope(str, Enum):
    """How broad a source's or record's coverage is."""

    NATIONAL = "NATIONAL"
    STATE = "STATE"
    DISTRICT = "DISTRICT"
    CITY = "CITY"
    MUNICIPALITY = "MUNICIPALITY"
    WARD = "WARD"


class ServiceCategory(str, Enum):
    """The four civic-service areas this knowledge base covers."""

    WASTE_SANITATION = "WASTE_SANITATION"
    WATER_DRAINAGE = "WATER_DRAINAGE"
    ROADS_POTHOLES = "ROADS_POTHOLES"
    STREETLIGHTS = "STREETLIGHTS"


# The fixed, exact string every SYNTHETIC record's source_organization must use — one constant,
# not re-typed at every call site, so it can never accidentally drift into something that reads
# as a real department name.
SYNTHETIC_SOURCE_ORGANIZATION = "JanSarthi AI — synthetic representative record (not sourced from an official document)"


class SourceRecord(BaseModel):
    """One citable source: a government webpage, PDF, dataset, or (for synthetic records) the
    fixed synthetic placeholder. This is the master catalogue every KnowledgeRecord.source_id
    must resolve against — see scripts/build_rag_knowledge_base.py's referential-integrity check.

    Attributes:
        source_id: Stable short identifier, e.g. "TN_MELUR_CITIZEN_CHARTER".
        source_title: Human-readable title, e.g. "Citizen Charter – Melur Municipality".
        source_organization: The publishing body, e.g. "Melur Municipality" — or the fixed
            SYNTHETIC_SOURCE_ORGANIZATION string for synthetic sources.
        source_url: The live URL, or None for a synthetic source (never a fabricated URL).
        source_type: What kind of artifact this is.
        verification_status: VERIFIED or SYNTHETIC — must agree with source_type (see validator).
        publication_date: When the source document/page was published, if stated. Often unknown
            for government webpages, hence optional.
        last_updated: When the source was last updated, if stated on the source itself.
        retrieved_at: When *this project* fetched/checked the source — always known, unlike the
            two dates above which depend on what the source itself discloses.
        geographic_scope: How broad this source's coverage is.
        notes: Free-text research notes (e.g. "PDF link only, page has no inline content";
            "SSL cert broken, verified via cached search snippet instead").
    """

    source_id: str
    source_title: str
    source_organization: str
    source_url: str | None = None
    source_type: SourceType
    verification_status: VerificationStatus
    publication_date: date | None = None
    last_updated: date | None = None
    retrieved_at: datetime
    geographic_scope: GeographicScope
    notes: str | None = None

    @model_validator(mode="after")
    def _verification_matches_type(self) -> "SourceRecord":
        is_synthetic = self.source_type == SourceType.SYNTHETIC_REPRESENTATIVE
        if is_synthetic and self.verification_status != VerificationStatus.SYNTHETIC:
            raise ValueError("A SYNTHETIC_REPRESENTATIVE source must have verification_status=SYNTHETIC.")
        if not is_synthetic and self.verification_status != VerificationStatus.VERIFIED:
            raise ValueError("A non-synthetic source_type must have verification_status=VERIFIED.")
        if is_synthetic:
            if self.source_url is not None:
                raise ValueError("A SYNTHETIC source must not carry a source_url.")
            if self.source_organization != SYNTHETIC_SOURCE_ORGANIZATION:
                raise ValueError(f"A SYNTHETIC source's source_organization must be exactly {SYNTHETIC_SOURCE_ORGANIZATION!r}.")
        else:
            if not self.source_url:
                raise ValueError("A VERIFIED source must carry a real source_url.")
        return self

    model_config = ConfigDict(use_enum_values=False)


class FAQItem(BaseModel):
    """One question/answer pair attached to a knowledge record."""

    question: str
    answer: str


class KnowledgeRecord(BaseModel):
    """One structured piece of civic-service knowledge for one place.

    Attributes mirror the spec directly: what service this is about, where, which department
    handles it, how a citizen engages with it, and — critically — exactly which source (and
    verification tier) every fact in this record traces back to.
    """

    record_id: str
    service_id: str
    service_name: str
    sub_service: str
    problem_type: str
    service_category: ServiceCategory

    state: str
    district: str | None = None
    # None for a genuinely STATE-scoped record (e.g. a state department's circular applying to
    # every Urban Local Body) -- forcing a specific city onto data that explicitly applies
    # everywhere would misrepresent the source. geographic_scope is what a consumer should
    # actually branch on; city is populated whenever the source itself names one.
    city: str | None = None
    municipality: str | None = None
    zone: str | None = None
    ward: str | None = None
    area: str | None = None
    geographic_scope: GeographicScope

    department: str
    authority: str

    description: str
    procedure: str
    required_information: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)

    sla: str | None = None
    expected_response_time: str | None = None
    expected_resolution_time: str | None = None

    complaint_channel: list[str] = Field(default_factory=list)
    contact_information: str | None = None

    escalation_procedure: str | None = None
    escalation_authority: str | None = None

    faq: list[FAQItem] = Field(default_factory=list)
    citizen_guidance: str | None = None

    # Source-citation block — same fields as SourceRecord, denormalized onto every record (and,
    # later, every Document/Chunk) so a UI can always show "Answer -> Source -> URL" from a
    # single record/chunk without a join.
    source_id: str
    source_title: str
    source_organization: str
    source_url: str | None = None
    source_type: SourceType
    verification_status: VerificationStatus
    publication_date: date | None = None
    last_updated: date | None = None
    retrieved_at: datetime

    created_at: datetime

    @model_validator(mode="after")
    def _verification_matches_type(self) -> "KnowledgeRecord":
        is_synthetic = self.source_type == SourceType.SYNTHETIC_REPRESENTATIVE
        if is_synthetic and self.verification_status != VerificationStatus.SYNTHETIC:
            raise ValueError(f"{self.record_id}: SYNTHETIC_REPRESENTATIVE source_type requires verification_status=SYNTHETIC.")
        if not is_synthetic and self.verification_status != VerificationStatus.VERIFIED:
            raise ValueError(f"{self.record_id}: non-synthetic source_type requires verification_status=VERIFIED.")
        if is_synthetic:
            if self.source_url is not None:
                raise ValueError(f"{self.record_id}: a SYNTHETIC record must not carry a source_url.")
            if self.source_organization != SYNTHETIC_SOURCE_ORGANIZATION:
                raise ValueError(f"{self.record_id}: a SYNTHETIC record's source_organization must be exactly {SYNTHETIC_SOURCE_ORGANIZATION!r}.")
        else:
            if not self.source_url:
                raise ValueError(f"{self.record_id}: a VERIFIED record must carry a real source_url.")
        return self


class Document(BaseModel):
    """A rendered, human-readable document for one KnowledgeRecord — GENERATED by
    scripts/build_rag_knowledge_base.py, never hand-authored. `content` is the record's
    structured fields rendered into prose, ready to be chunked.

    zone/ward/area/geographic_scope were added alongside state/district/city/municipality (see
    the location migration's §13) so a record's full location precision -- wherever the source
    KnowledgeRecord actually has it -- survives past this rendering step; previously only
    state/district/city/municipality were copied down and ward/area/zone were silently dropped
    even on the rare record that had them. All four remain None whenever the source record's
    own field is None -- this only stops discarding data that was already there, it never adds
    any.

    service_category was added during the ChromaDB migration (see
    docs/ask_sarthi_rag_architecture.md) so metadata filtering can do an exact match on this
    field directly, instead of the retriever inferring category from a `service_id` string-prefix
    convention (fragile, and unsupported by ChromaDB's `where` filter syntax, which has no
    string-prefix operator). Always present -- `KnowledgeRecord.service_category` is a required
    field, never inferred or guessed here."""

    document_id: str
    record_id: str
    title: str
    content: str

    state: str
    district: str | None = None
    city: str | None = None
    municipality: str | None = None
    zone: str | None = None
    ward: str | None = None
    area: str | None = None
    geographic_scope: GeographicScope
    service_category: ServiceCategory
    service_id: str
    sub_service: str
    department: str

    source_id: str
    source_title: str
    source_organization: str
    source_url: str | None = None
    source_type: SourceType
    verification_status: VerificationStatus


class Chunk(BaseModel):
    """One retrieval-ready chunk of a Document — GENERATED, never hand-authored. Carries the
    full citation block so a future retrieval pipeline can always reconstruct
    "Answer -> Source title -> Organization -> clickable URL" from a chunk alone, with no join
    back to the parent Document or KnowledgeRecord required.

    zone/ward/area/geographic_scope/service_category: see Document's docstring -- same
    reasoning, carried one level further down to the actual filterable/retrievable unit (this is
    exactly the metadata dict ChromaDB stores alongside each chunk's embedding -- see
    scripts/build_rag_embeddings.py)."""

    chunk_id: str
    document_id: str
    content: str

    state: str
    district: str | None = None
    city: str | None = None
    municipality: str | None = None
    zone: str | None = None
    ward: str | None = None
    area: str | None = None
    geographic_scope: GeographicScope
    service_category: ServiceCategory
    service_id: str
    sub_service: str
    department: str

    source_id: str
    source_title: str
    source_organization: str
    source_url: str | None = None
    source_type: SourceType
    verification_status: VerificationStatus


class TestQuestion(BaseModel):
    """One RAG evaluation question, hand-authored (not generated)."""

    # Not a pytest test class -- pytest's collector flags any top-level `TestXxx` name as a
    # candidate; this silences that false positive without renaming a class that's correctly
    # named for what it actually is (a schema for stored test-question data, not test code).
    __test__ = False

    question_id: str
    language: str
    question_text: str
    expected_record_ids: list[str] = Field(default_factory=list)
    expected_answer_gist: str
    expects_citation: bool = True
    notes: str | None = None

    @model_validator(mode="after")
    def _language_is_supported(self) -> "TestQuestion":
        if self.language not in settings.SUPPORTED_LANGUAGES:
            raise ValueError(
                f"{self.question_id}: language {self.language!r} is not one of "
                f"backend.config.settings.SUPPORTED_LANGUAGES ({sorted(settings.SUPPORTED_LANGUAGES)})."
            )
        return self
