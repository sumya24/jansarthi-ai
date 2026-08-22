# Ask Sarthi Knowledge Base — Real vs. Synthetic, by Location Level (Research Prep)

**What this file is for:** a decision-making reference, not a to-do list. Before picking what to
research next, this shows exactly which geographic levels the knowledge base actually stores data
at, and which states/cities are already real vs. still leaning on placeholder content — so you can
point at specific states and say "research this" or "leave this alone."

**No research has been started from this file.** Checked directly against the 241 record files on
disk on **2026-08-20**.

**Scope note — this file is ONLY about the knowledge base (JSON files under
`data/rag_knowledge_base/knowledge_records/`), not the SQL location hierarchy** (`states`,
`districts`, `wards`, etc. in `janmitra.db`, covered in `DATA_COVERAGE_TRACKER.md`). Those are two
separate systems that don't currently reference each other — mixing them into one table earlier
caused confusion, so this file deliberately stays on one side only.

---

## 1. Which geographic levels does the knowledge base actually use?

Every knowledge record *can* carry 5 location fields: `state`, `district`, `city`,
`municipality`, `zone`, `ward`, `area`. Checked how many of the 241 real records actually have
each one filled in:

| Field | Verified records (133 total) | Synthetic records (112 total) | Actually usable today? |
|---|---|---|---|
| **State** | 133 / 133 (100%) | 112 / 112 (100%) | ✅ Yes — every record has one |
| **City** | 120 / 133 (90%) | 112 / 112 (100%) | ✅ Yes — almost every record has one |
| **Municipality** | 125 / 133 (94%) | 112 / 112 (100%) | ✅ Yes — almost every record has one |
| **District** | 25 / 133 (19%) | 0 / 112 (0%) | ⚠️ Barely — only on some *verified* records, never on synthetic ones |
| **Zone** | 0 / 133 (0%) | 0 / 112 (0%) | ❌ No — never populated once, on any record, ever |
| **Ward** | 0 / 133 (0%) | 0 / 112 (0%) | ❌ No — never populated once, on any record, ever |
| **Area** | 0 / 133 (0%) | 0 / 112 (0%) | ❌ No — never populated once, on any record, ever |

*(4 new verified records added 2026-08-21 for Andhra Pradesh have `city: null` on purpose — see
the update note in §2 — which is why City's percentage dropped slightly even as the verified
total went up.)*

**Practical takeaway:** research effort at the **Zone/Ward/Area level would be wasted right now**
— those fields exist in the schema but nothing in the whole knowledge base has ever used them, so
there's no partial data to complete. **State/City/Municipality are where real content actually
lives.** District is a genuine partial gap — worth knowing which 6 districts already have it (below).

### The only 6 districts ever recorded (all from verified sources, none synthetic)
- Kerala — Ernakulam (city: Kochi)
- Punjab — S.A.S. Nagar / Mohali
- Punjab — Patiala
- Rajasthan — Jaipur
- Tamil Nadu — Coimbatore
- West Bengal — Kolkata

Every other verified or synthetic record — all 235 of them — has no district value at all.

---

## 2. Real vs. synthetic record counts, per state (the actual research-priority list)

| State | Verified (real) | Synthetic (placeholder) | Status |
|---|---:|---:|---|
| Odisha | 7 | 0 | ✅ Fully real already — nothing to research |
| Punjab | 12 | 0 | ✅ Fully real already — nothing to research |
| Delhi | 8 | 4 | 🟢 Mostly real (2:1) |
| Maharashtra | 9 | 8 | 🟡 About half real |
| Tamil Nadu | 9 | 8 | 🟡 About half real |
| Telangana | 9 | 8 | 🟡 About half real |
| West Bengal | 9 | 8 | 🟡 About half real |
| Bihar | 7 | 8 | 🟡 About half real |
| Gujarat | 8 | 8 | 🟡 About half real |
| Haryana | 8 | 8 | 🟡 About half real |
| Kerala | 8 | 8 | 🟡 About half real |
| Rajasthan | 8 | 8 | 🟡 About half real |
| Uttar Pradesh | 8 | 8 | 🟡 About half real |
| **Andhra Pradesh** | **8** | **8** | 🟡 **About half real — up from 4V+8S on 2026-08-21, see below** |
| Assam | 4 | 4 | 🟡 About half real |
| Karnataka | 6 | 8 | 🟠 More placeholder than real |
| Madhya Pradesh | 5 | 8 | 🟠 More placeholder than real |
| Chandigarh (UT) | 4 | 0 | ✅ Fully real — closed 2026-08-21, was zero coverage |
| Goa | 4 | 0 | ✅ Fully real — closed 2026-08-21, was zero coverage (Streetlights added same day via the Electricity Department, a different department from the other 3) |
| Jharkhand | 4 | 0 | ✅ Fully real — closed 2026-08-21, was zero coverage (Ranchi, via Smart Ranchi) |
| Uttarakhand | 4 | 0 | ✅ Fully real — closed 2026-08-21, was zero coverage (Dehradun; Roads/Potholes added same day via the Public Work Department) |
| Himachal Pradesh | 4 | 0 | ✅ Fully real — closed 2026-08-21, was zero coverage (Shimla, via its own Citizen Charter) |
| Chhattisgarh | 3 | 0 | 🟢 3 of 4 categories real, Streetlights genuinely not found — closed 2026-08-21, was zero coverage |
| Puducherry (UT) | 4 | 0 | ✅ Fully real — closed 2026-08-21, was zero coverage (Oulgaret; Roads/Streetlights added same day via the Engineering Section) |
| Jammu and Kashmir (UT) | 4 | 0 | ✅ Fully real — closed 2026-08-21, was zero coverage (Srinagar) |
| *(11 other states/UTs)* | 0 | 0 | ⚫ No data at all — not a "replace synthetic" case, a "start from zero" case |

**Sixth update, 2026-08-21:** Puducherry's remaining 2 categories closed (Oulgaret's own
Engineering Section page explicitly names "Street Lighting" and street/drain maintenance) --
Puducherry now full 4/4. Chhattisgarh's Streetlights gap was explicitly retried (district
electricity listing, the power company's own site, a dead RMC domain) and remains genuinely open.
Jammu and Kashmir (Srinagar) added fresh, full 4/4. 164 verified records total, 276 overall. See
`candidate_urls.md` Round 18.

**Fifth update, 2026-08-21:** 3 more states researched, picking larger/more nationally-visible
ones first per explicit request. Himachal Pradesh (Shimla) closed to full 4/4 via its own Citizen
Charter (a real government document naming each department's function) plus a verbatim-confirmed
Street Light toll-free number. Chhattisgarh (Raipur) and Puducherry (Oulgaret) both moved from
zero to partial coverage. See `candidate_urls.md` Round 17.

**Fourth update, 2026-08-21:** Uttarakhand's Roads/Potholes gap closed too (Nagar Nigam Dehradun's
Public Work Department, a named Executive Engineer with direct contact). Uttarakhand now full
4/4. See `candidate_urls.md` Round 16.

**Third update, 2026-08-21:** Goa's Streetlights gap closed (Electricity Department, a genuinely
different department from the ULB portal used for its other 3 categories), plus Jharkhand (4/4,
Ranchi) and Uttarakhand (3/4 at the time, Dehradun) moved off the zero-coverage list. 15 states
still at zero (down from 19 at session start). See `candidate_urls.md` Round 15.

**Second update, 2026-08-21:** 2 states moved off the zero-coverage list entirely — Chandigarh
(4/4 categories, general channel via mcchandigarh.gov.in) and Goa (3/4 at the time — Streetlights
genuinely not found yet, see the update above). See `candidate_urls.md` Round 14.

**First update, 2026-08-21:** Andhra Pradesh (then the single worst-ratio state) was researched —
`cdma.ap.gov.in` (the state Commissioner & Director of Municipal Administration's own live
grievance page) named all 4 service categories with a real submission channel, verified via two
independent fetches. Added as 4 new VERIFIED, state-wide records (not tied to any one city, since
the source itself makes no city-specific claim) — see
`data/rag_knowledge_base/sources/candidate_urls.md` Round 12. Karnataka and Madhya Pradesh are now
the two most placeholder-reliant states.

Sorted worst-to-best by how much of each state's content is still placeholder. If the goal is
"stop showing citizens synthetic content first," Andhra Pradesh, Madhya Pradesh, and Karnataka are
the top 3 candidates; Odisha and Punjab need nothing.

---

## 3. City-level breakdown (for filtering)

`rag_city_breakdown.csv`, in this same folder, lists every city with its real (`Verified_Records`)
and placeholder (`Synthetic_Records`) counts — open it in Excel/Sheets and filter/sort to find,
e.g., "every city where Synthetic_Records > Verified_Records."

**Two cities are 100% synthetic — 0 real records at all — and both look like obvious next
targets, but aren't:**

- **Vijayawada, Andhra Pradesh** (0 verified, 4 synthetic)
- **Bhopal, Madhya Pradesh** (0 verified, 4 synthetic)

Both were already researched extensively in prior rounds (`sources/candidate_urls.md`, Round 11
for Vijayawada, Round 10 for Bhopal) — every plausible official domain for each was tried and
confirmed dead (client-side-only pages, deprecated URL structures, an expired/parked domain, a
non-resolving DNS name). **Re-trying the same angles on these two would just repeat already-logged
dead ends** — real progress here needs a genuinely new lead (a different department, a different
portal, an RTI-style request), not another guess at the same municipal corporation's website.

---

## 4. Decision checklist for picking what to research next

For each state/city you're considering, this file already answers:

- [ ] Does it have **any** real (verified) content at all? (§2 — if 0, it's a from-scratch research
      target, not a replacement target)
- [ ] How much of what's there is still placeholder? (§2's ratio column)
- [ ] Does it have district-level detail, or only city-level? (§1's district list)
- [ ] Is Zone/Ward/Area-level detail worth chasing? (§1 — no, not yet, for any state)

Once you've picked specific states/cities/categories, the actual research method already exists
and doesn't need to be reinvented — `data/rag_knowledge_base/sources/candidate_urls.md` documents
11 rounds of exactly this work (search for a real government source, verify the content actually
supports the claim, log a promotion or an honest dead end). Continuing from there means picking up
the same log, not starting a new process.
