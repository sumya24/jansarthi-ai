# JanSarthi AI — RAG Knowledge Base: Data Foundation

This folder is **research and data collection only** — it is preparation
for a future RAG (retrieval-augmented generation) knowledge base that
will eventually let JanSarthi AI ground complaint handling in real,
official government information (which department handles what, SLAs,
escalation paths, official contact channels) instead of just translating
and summarizing complaints as the app does today.

**Nothing here is a pipeline, an agent, an API, embeddings, or a vector
database.** It is the data foundation those things would eventually be
built on top of: verified official sources, structured into a consistent
schema, with every fact traceable to a real URL.

## Ground rules (apply to every file in this tree)

### 1. Official sources only
Usable, verifiable sources: state government websites, municipal
corporation / municipal council websites, Urban Development / Housing &
Urban Development / Municipal Administration departments, official
citizen grievance portals, official citizen charters, official government
PDFs, `data.gov.in`, state Open Government Data (OGD) portals, Smart
Cities Mission datasets, official department reports and notifications.

**Never used as a verified fact source**: blogs, news articles, private
websites, Wikipedia, random data aggregators. (A third-party source may
be *logged* for reference — see Quality rating D below — but never used
to fill in a real field.)

### 2. Never fabricate
If a fact is not stated in an official source, the field is filled with
the exact sentinel value for that situation — never inferred, never
guessed, never copied from a similar-looking city or state:

| Situation | Sentinel value |
|---|---|
| Field/fact not published anywhere found | `NOT FOUND IN OFFICIAL SOURCE` |
| SLA/response-time specifically not published | `SLA NOT FOUND` |
| Escalation process specifically not published | `ESCALATION INFORMATION NOT FOUND` |
| Source exists but is stale/dated and needs re-checking before real use | `OUTDATED / VERIFY BEFORE PRODUCTION` |

A `NOT FOUND` or `PARTIAL` result is a legitimate, expected research
outcome — not something to "fix" by inventing plausible-sounding data.
Uneven coverage across states/cities/services is realistic and correct.

### 3. Geography is never assumed to transfer
A fact true for Tamil Nadu's water-leakage SLA says nothing about
Maharashtra's. Every record carries its own full geographic scope
(state/district/city/municipality/zone/ward) and is never reused,
copied, or extrapolated across a different jurisdiction.

### 4. Every source needs a real URL
`source_url` must be the exact page or document actually fetched — never
a bare domain name, never "official website," never a guess at what a
URL "should" be. If a URL couldn't be independently confirmed to
resolve, it does not get used.

### 5. Quality rating (apply to every source)

| Rating | Meaning |
|---|---|
| **A** | Official + directly relevant + location-specific (names this exact service/location, states SLA/procedure explicitly) |
| **B** | Official + relevant but general (state/national-level policy that applies broadly, not tied to one city) |
| **C** | Official structured dataset (OGD/data.gov.in CSV, API, etc.) — useful for analytics/SQL, not citizen-facing procedural text |
| **D** | Third-party / reference-only — logged for context, **never** treated as a verified fact |

Primary RAG content should preferably come from A and B sources.

### 6. What must NOT go into this knowledge base
Citizen IDs, worker IDs, complaint IDs, mock worker data, live complaint
status, assignment info, internal JanSarthi workflow data, or any private
citizen information. This is a *government-facts* knowledge base, not a
copy of JanSarthi's own transactional data — that stays in the app's own
database (`backend/models.py`).

## The 4 civic service categories in scope

See `01_service_data_requirements.md` for the full breakdown, but at a
glance:

1. **Waste & Public Sanitation** — garbage collection (incl. missed
   collection), illegal dumping, street/public-area cleanliness, public
   toilet sanitation, waste/debris removal
2. **Water & Drainage** — water leakage, no/low supply, contaminated
   water, pipeline problems, drain blockage/overflow, sewage/drainage
3. **Roads & Potholes** — potholes, damaged roads, road maintenance,
   broken footpaths, road obstruction/damage
4. **Streetlights** — not working, damaged streetlight/pole, lighting
   problems

## Per-record template

Every fact captured anywhere in `02_source_inventory/` uses this exact
block, so the compile step (`03_official_source_inventory.md` onward)
never has to re-derive structure. Fields map 1:1 onto the canonical
schema in `08_kb_schema.md`.

```
### Record: <State>-<City>-<Service>[-<SubService>]

service_id:              <short slug, e.g. mh-pune-waste-garbage-collection>
service_name:
sub_service:
problem_type:

state:
district:
city:
municipality:
zone:
ward:                     (or "NOT FOUND IN OFFICIAL SOURCE" if not applicable/published)

department:
authority:
officer_designation:      (only if officially published; otherwise NOT FOUND IN OFFICIAL SOURCE)

description:
procedure:
required_information:
required_documents:

sla:                       (or SLA NOT FOUND)
response_time:
resolution_time:

complaint_channel:
contact_information:

escalation_procedure:      (or ESCALATION INFORMATION NOT FOUND)
escalation_authority:

faq:
citizen_guidance:

source_title:
source_url:                 <exact, fetched, resolvable URL>
source_type:                 (citizen_charter | official_pdf | ogd_dataset | govt_portal | grievance_portal | smart_city_dataset | dept_notification)
source_organization:

publication_date:
last_updated:
retrieved_at:                (actual date this record was researched)

verification_status:         (verified | outdated_needs_reverification)
source_quality:               (A | B | C | D)
geographic_scope:             (state | district | city | municipality | ward)
notes:
```

## Per-state summary table (top of every `02_source_inventory/<state>.md`)

```
| State | City | Service | Source | Authority | URL | Format | Data Available | RAG or SQL | Quality |
|---|---|---|---|---|---|---|---|---|---|
```

## Canonical schema
See `08_kb_schema.md` for the full 34-field schema this all compiles into.

## File map
This folder's files are numbered in the order they're meant to be produced/read:
`00` (this file) → `01` (scope) → `02_source_inventory/` (raw per-state
research) → `03`–`15` (compiled deliverables, derived from `02_` only,
no new research introduced at that stage).
