/**
 * Backend-ready contracts for the RAG/"Ask Sarthi" and civic-service concepts this phase
 * builds UI for, but does not implement the backend of (see docs — Track A is preparing the
 * real knowledge base separately).
 *
 * These field names and enum values are deliberately copied verbatim from the real schemas
 * already defined by that track (backend/schemas/rag_knowledge.py and
 * data/rag_knowledge_base/schema/*.schema.json) rather than invented independently, so wiring
 * up the real backend later is a drop-in, not a remapping exercise. Do not rename these
 * concepts without a corresponding reason to remap on both sides.
 */

/** The four civic-service areas this app covers. Matches backend ServiceCategory exactly. */
export type ServiceCategory = "WASTE_SANITATION" | "WATER_DRAINAGE" | "ROADS_POTHOLES" | "STREETLIGHTS";

export const SERVICE_CATEGORIES: ServiceCategory[] = [
  "WASTE_SANITATION",
  "WATER_DRAINAGE",
  "ROADS_POTHOLES",
  "STREETLIGHTS",
];

/** What kind of official artifact (or lack thereof) a source is. Matches backend SourceType. */
export type SourceType =
  | "OFFICIAL_GOVERNMENT_WEBPAGE"
  | "OFFICIAL_GOVERNMENT_PDF"
  | "OFFICIAL_OGD_DATASET"
  | "OFFICIAL_GOVERNMENT_API"
  | "SYNTHETIC_REPRESENTATIVE";

/** Whether a record has been individually checked against a real, live source. */
export type VerificationStatus = "VERIFIED" | "SYNTHETIC";

/** How broad a source's coverage is. */
export type GeographicScope = "NATIONAL" | "STATE" | "DISTRICT" | "CITY" | "MUNICIPALITY" | "WARD";

/** One citable source, exactly matching backend SourceRecord's fields — the shape SourceCard
 * consumes. A real backend response will have url: null for synthetic sources; never fabricate
 * one client-side. */
export interface SourceRecord {
  source_id: string;
  source_title: string;
  source_organization: string;
  source_url: string | null;
  source_type: SourceType;
  verification_status: VerificationStatus;
  geographic_scope: GeographicScope;
  publication_date?: string | null;
  last_updated?: string | null;
}

/** The real Ask Sarthi backend's response shape (backend/schemas/ask_janmitra.py's
 * AskJanMitraResponse — POST /ask-janmitra, wired in lib/api.ts's askJanMitra() and consumed by
 * pages/AskJanMitra.tsx). Superset of the original `{answer, sources}` shape this page rendered
 * against its now-removed mock data (lib/mockAskJanMitra.ts, deleted once this real backend
 * existed) — `sources` alone remains valid for that render code, everything else here is
 * additive (intent/location/follow-up/routing metadata). */
export interface AskJanMitraLocation {
  city: string | null;
  state: string | null;
  source: "text" | "gps" | "conversation_history" | "none";
  is_ambiguous: boolean;
  ambiguous_candidates: string[];
}

export type AskJanMitraIntent =
  | "TYPE_A_COMPLAINT"
  | "TYPE_B_SERVICE_INFO"
  | "TYPE_C_STATUS"
  | "CAPABILITIES"
  | "UNCLEAR";

/** A reference to a photo already validated and saved to disk (backend/schemas/ask_janmitra.py's
 * PhotoEvidenceRef, mirroring evidence_service.SavedFile) -- round-tripped on
 * AskJanMitraConversationTurn so a LATER turn with no photo of its own (e.g. a typed "yes, submit
 * it") can still have the SAME real file attached to the complaint it eventually creates. See
 * AskJanMitra.tsx's `historyForRequest` for where this gets echoed back. */
export interface PhotoEvidenceRef {
  filename: string;
  original_name: string;
  content_type: string;
  size: number;
}

export interface AskJanMitraResponse {
  answer: string;
  intent: AskJanMitraIntent;
  service_category: ServiceCategory | null;
  language: string;
  location: AskJanMitraLocation | null;
  sources: SourceRecord[];
  verification_status: VerificationStatus | "MIXED" | null;
  follow_up_required: boolean;
  follow_up_question: string | null;
  follow_up_options: string[];
  /** Same length/order as `follow_up_options`, translated into `language` for DISPLAY only --
   * `follow_up_options` itself always stays the canonical English text a click sends back (see
   * backend/services/orchestration/nodes.py's `_localize_options` docstring for why). null
   * whenever the backend didn't produce localized labels for this turn (no follow-up at all, or
   * the dynamic ambiguous-location city-name options, deliberately left untranslated) -- render
   * code should fall back to `follow_up_options` itself in that case. */
  follow_up_options_labels: string[] | null;
  insufficient_knowledge: boolean;
  routed_to:
    | "RAG"
    | "COMPLAINT_CREATED"
    | "COMPLAINT_STATUS_API"
    | "NONE_OUT_OF_SCOPE"
    | "NONE_CLARIFICATION_NEEDED"
    /** A prompt-injection guardrail blocked this turn before it ever reached an LLM -- see
     * backend/services/guardrails.py. */
    | "NONE_BLOCKED_GUARDRAIL"
    /** A genuinely multi-category question (e.g. a flooded street + a blocked drain + a broken
     * streetlight in one message), answered per-category then combined -- see
     * backend/services/orchestration/nodes.py's agent_flow_node. */
    | "RAG_MULTI_CATEGORY";
  answer_was_llm_generated: boolean;
  /** Set only when routed_to === "COMPLAINT_CREATED" — see backend/services/orchestration/
   * nodes.py's complaint_flow_node (LangGraph orchestration phase). A TYPE_A complaint-shaped
   * message with enough information now files a real complaint via the same pipeline the
   * dedicated complaint form uses, instead of only answering from RAG. */
  complaint_id: number | null;
  /** Set whenever a photo was validated and saved to disk THIS turn -- see PhotoEvidenceRef's own
   * docstring. Echoed back via AskJanMitraConversationTurn.photo_evidence so a LATER turn (no
   * photo of its own) can still recover and attach the SAME file to a complaint. */
  photo_evidence?: PhotoEvidenceRef | null;
  /** One of "DRAFT" | "AWAITING_CONFIRMATION" | "CONFIRMED" | "CANCELLED", or null when this turn
   * wasn't complaint-shaped at all -- see backend/schemas/ask_janmitra.py's own docstring.
   * LIVE-REPORTED BUG this closes: this field existed on the backend from the start specifically
   * so Sarthi could recognize its own confirmation/terminal turns as DATA rather than by
   * re-parsing this response's own (possibly translated) `answer` text -- but this TS type never
   * declared it, so it silently never round-tripped. Every one of those checks fell back to
   * matching fixed ENGLISH substrings against `answer`, which a citizen conversing in Hindi/
   * Marathi/Odia/Gujarati/Bengali never produces, so a FILED complaint's own turn was never
   * recognized as a boundary -- a brand-new complaint in a DIFFERENT city, right after, silently
   * reused the already-closed one's ward/category instead of resolving fresh. Echoed back via
   * AskJanMitraConversationTurn.complaint_workflow_state, exactly like photo_evidence above. */
  complaint_workflow_state?: string | null;
}

export interface AskJanMitraConversationTurn {
  role: "user" | "assistant";
  content: string;
  photo_evidence?: PhotoEvidenceRef | null;
  complaint_workflow_state?: string | null;
}

/** POST /ask-janmitra/voice's response (backend/schemas/ask_janmitra.py's AskVoiceResponse) --
 * everything AskJanMitraResponse already has, plus the citizen's own transcribed speech
 * (`question`, a real STT result, not a guess) and the AI's reply as real synthesized audio.
 * `audio_base64` is null only when TTS itself failed server-side (a real, honest failure) --
 * `answer` is still real text to fall back to displaying in that case. */
export interface AskVoiceResponse extends AskJanMitraResponse {
  question: string;
  audio_base64: string | null;
  audio_format: string;
}

/** What the future AI service-identification step returns when a citizen describes a
 * complaint. `confidence` is deliberately nullable and MUST NOT be rendered as a percentage
 * unless a real backend actually supplies a meaningful value — see AIUnderstandingCard.tsx. */
export interface AIServiceIdentification {
  service: ServiceCategory;
  sub_service: string;
  confidence: number | null;
  location: string | null;
  language: string;
}

/** The extended, backend-friendly complaint status vocabulary from the Phase 1 spec. The real
 * API today only ever returns the first four (see lib/api.ts's ComplaintStatus) — the rest
 * exist here so StatusBadge and any future backend work share one vocabulary from day one,
 * not so the frontend can fabricate states the backend never sends. */
export type ComplaintLifecycleStatus =
  | "SUBMITTED"
  | "ASSIGNED"
  | "ACCEPTED"
  | "IN_PROGRESS"
  | "RESOLVED"
  | "REJECTED"
  | "ESCALATED"
  | "CANCELLED";

/** What a future "why is my complaint delayed" backend response is expected to combine: real
 * complaint timestamps (SQL) with SLA/expectation data (RAG). Every field here is optional
 * because, until that backend exists, a real complaint only ever has some of this — never
 * invent the missing parts (see ComplaintDetails.tsx's mock, which is clearly labeled). */
export interface DelayExplanation {
  status: ComplaintLifecycleStatus;
  assigned_at: string | null;
  sla: string | null;
  expected_resolution_time: string | null;
  last_update: string | null;
  is_overdue: boolean;
  is_escalated: boolean;
}
