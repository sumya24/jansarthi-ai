# RAG Knowledge Base — Source Research Log

## Round 18 (2026-08-21, session 12 continued): Puducherry closed to 4/4; Chhattisgarh Streetlights retried (still open); Jammu and Kashmir added (4/4)

Explicit follow-up on Round 17's 2 open threads (Puducherry, Chhattisgarh), then continued to a
new state.

| Gap | Angle tried | Result | Notes |
|---|---|---|---|
| **Oulgaret, Puducherry — Roads/Potholes and Streetlights (Round 17's open threads)** | oulmun.in's own "Engineering" services page (oulmun.in/engineering.php), not previously checked (only the Grievance Redressal page was checked in Round 17) | **PROMOTED TO VERIFIED (both)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/puducherry/oulgaret.json (PY_OULMUN_ROADS_POTHOLES, PY_OULMUN_STREETLIGHTS). Fetched directly. Explicitly, verbatim lists 'Construction and maintenance of public streets and drains' and 'Street Lighting' as named Engineering Section services -- direct confirmation, not inferred. Named contact: Web Information Manager S. Paramesvary, dpa-om@py.gov.in, 0413 2200812. Puducherry (Oulgaret) now full 4/4.]** |
| **Raipur, Chhattisgarh — Streetlights (Round 17's open thread)** | raipur.gov.in's district electricity public-utility listing; CSPDCL's (Chhattisgarh's power company) own site; rmc.nic.in/streetLight.html (a differently-titled page surfaced via search) | **CONFIRMED DEAD END (this round's angles)** | **[CHECKED — raipur.gov.in/en/public-utility-category/electricity/ names only a general CSPDCL contact (0771-2574166, webadmin@cspc.co.in), with no streetlight-specific text. cspdcl.co.in itself was searched explicitly for "street light"/"streetlight" and found zero matches -- it's a bare redirect shell. rmc.nic.in/streetLight.html (a real-looking, specifically-titled page) was confirmed dead via direct curl (connection refused), independent of WebFetch. No Raipur-specific streetlight department or number was found. Streetlights remains open for Chhattisgarh.]** |
| **Srinagar, Jammu and Kashmir — all 4 categories (previously 0 coverage)** | Srinagar Municipal Corporation's own homepage (smcsrinagar.jk.gov.in), specifically its Grievance Redressal/JK SAMADHAN description | **PROMOTED TO VERIFIED (all 4)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/jammu_and_kashmir/srinagar.json (JK_SMC_* x4). Fetched directly, twice (once for navigation, once re-checking the exact wording before trusting it) -- both agree verbatim: "Register and track grievances related to civic services, sanitation, water supply, roads, street lighting, and other municipal issues," linking to the state's unified grievance system JK SAMADHAN (samadhan.jk.gov.in/login, login-gated beyond this point). SMC's own separate "Sanitation Services" and "Drinking Water" menu items independently corroborate 2 of the 4 categories as real administered services. No dedicated Roads/PWD-equivalent contact found separately (unlike Uttarakhand/Chhattisgarh's Executive Engineer pattern) -- Roads and Streetlights rely on the general description alone, disclosed honestly in both records.]** |

### Net result of Round 18

6 new VERIFIED records (158 -> 164; 270 -> 276 total records; 953 -> 971 chunks in the live
ChromaDB index). Puducherry reaches full 4/4 (the 5th state fully closed this session). Jammu and
Kashmir moves from 0 to full 4/4. Chhattisgarh's Streetlights gap remains genuinely open after a
real, logged attempt. 11 states remain at zero coverage (down from 12 at the start of this round).

## Round 17 (2026-08-21, session 12 continued): 3 more zero-coverage states -- Chhattisgarh (3/4), Himachal Pradesh (4/4), Puducherry (2/4)

Continued down the zero-coverage list, picking states with likely-larger populations/more national
visibility per explicit instruction to prioritize "popular" ones first.

| Gap | Angle tried | Result | Notes |
|---|---|---|---|
| **Raipur, Chhattisgarh — 3 of 4 (previously 0 coverage)** | nagarnigamraipur.nic.in's own Contact Us page | **PROMOTED TO VERIFIED (3 of 4 -- Streetlights explicitly NOT found)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/chhattisgarh/raipur.json. Real main line (+91-771-2535780-2535790), named Additional Commissioners, and 10 zones each with a named Zone Commissioner + Executive Engineer with direct mobile numbers. Waste/Water used the general channel (same tier as Chandigarh); Roads/Potholes used the zone Executive Engineers (same PWD-equivalent reasoning as Uttarakhand's record). The official state grievance portal (nidaan.cg.gov.in, "NIDAAN 1100") and Smart City Raipur (smartcityraipur.cgstate.gov.in) were both checked but are JS-rendered SPA shells with nothing fetchable beyond a phone number -- not used. No Streetlight-specific department or number found anywhere.]** |
| **Shimla, Himachal Pradesh — all 4 categories (previously 0 coverage)** | MC Shimla's own Citizen Charter (shimlamc.hp.gov.in/CitizenCharter/Index) plus its homepage flash-news | **PROMOTED TO VERIFIED (all 4)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/himachal_pradesh/shimla.json. The Citizen Charter is a real government document (same tier as Odisha's HUDD Citizen Charter) explicitly describing each department's function in its own words: Road & Building Department, Water System & Sewerage Department, and Health Branch (explicitly "door to door garbage collection, Street Sweeping"). Separately, the homepage's own flash-news carousel verbatim states "Street Light Complaint Toll Free Number 1800-180-3580" -- independently re-confirmed on the official domain (not just trusted from a third-party aggregator that first surfaced the claim). No numeric SLA found for any department -- the Charter itself says so.]** |
| **Oulgaret, Puducherry — 2 of 4 (previously 0 coverage)** | oulmun.in's own Grievance Redressal page | **PROMOTED TO VERIFIED (2 of 4 -- Roads/Potholes and Streetlights explicitly NOT found)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/puducherry/oulgaret.json. Genuinely category-specific contacts, not one general line reused: "Garbage & Side drain Complaints" (91183 83911, 10 AM-6 PM) and "Underground Drainage Complaints (PWD)" (0413-2336076/2336068, office hours). Explicitly searched the full page text for "road"/"pothole"/"street light" -- only an unrelated "Road Classification" government-order document link found, no complaint channel for either category. Pondicherry Municipality's own equivalent page only links to a bare login-gated portal (lgredressal.py.gov.in/pgrs/) with no category detail visible.]** |

### Net result of Round 17

9 new VERIFIED records (149 -> 158; 261 -> 270 total records; 926 -> 953 chunks in the live
ChromaDB index). Himachal Pradesh reaches full 4/4 (the 4th state fully closed this session,
alongside Chandigarh, Jharkhand, and Uttarakhand). Chhattisgarh and Puducherry move from 0 to
partial coverage, each with an honestly-disclosed remaining gap. 12 states remain at zero coverage
(down from 15 at the start of this round).

## Round 16 (2026-08-21, session 12 continued): Uttarakhand's Roads/Potholes closed -- Dehradun reaches 4/4

Explicit follow-up on Round 15's one open thread for Dehradun.

| Gap | Angle tried | Result | Notes |
|---|---|---|---|
| **Dehradun, Uttarakhand — Roads/Potholes (the one category Round 15 left open)** | nagarnigamdehradun.com/public-work-department.php -- a second official Nagar Nigam Dehradun web presence, linked from nndehradun.uk.gov.in's own Contact Us page | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttarakhand/dehradun.json (UK_NNDDN_ROADS_POTHOLES). Fetched directly, twice (once for contacts, once explicitly checking for a departmental-remit description). Names 3 real officers with direct contact: Executive Engineer Rajit Kothyal (9997943221), 2 Junior Engineers, department line +91-135-2653572. Honest caveat logged in the record itself: the page has no explicit "this department handles roads/potholes" sentence -- this record relies on "Public Work Department" (PWD) being standard, well-established Indian municipal terminology for the roads/civil-infrastructure department, the same evidentiary tier as WELL_ESTABLISHED_PUBLIC_GEOGRAPHY facts already used elsewhere in this project, not a stretch from unrelated wording the way NDMC's Round-10 case was found to be.]** |

### Net result of Round 16

1 new VERIFIED record (148 -> 149; 260 -> 261 total records; 923 -> 926 chunks in the live
ChromaDB index). Uttarakhand reaches full 4/4 -- the 3rd state closed to completion this session
(alongside Chandigarh and Goa; Jharkhand also already at 4/4).

## Round 15 (2026-08-21, session 12 continued): Goa's Streetlights closed; Jharkhand (4/4) and Uttarakhand (3/4) added

Explicit follow-up on Round 14's one open thread (Goa Streetlights), then continued down the
zero-coverage list.

| Gap | Angle tried | Result | Notes |
|---|---|---|---|
| **Goa — Streetlights (the one category Round 14 left open)** | goaelectricity.gov.in (Electricity Department, Government of Goa) -- a genuinely different department from the municipal ULB portal used for Goa's other 3 categories | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/goa/statewide.json (GA_ELECTRICITY_STREETLIGHTS, appended as a 4th record). The department's own site has a section literally titled "Open Access & Streetlight Matters", confirmed via direct fetch -- establishing this (not the ULB portal, which has no streetlight option) as the correct department. That specific page is legal/billing content (Public Lighting Duty Act) with no dedicated complaint form, so the department's general contact channel is used instead: toll-free 1912, 91-832-2485500 (outside Goa), customersupport@goaelectricity.gov.in -- all confirmed on the same domain's Contact Us page. Goa now has all 4 categories.]** |
| **Ranchi, Jharkhand — all 4 categories (previously 0 coverage)** | Smart Ranchi 24x7 connect center (smartranchi.in), linked directly from Ranchi Municipal Corporation's own official site | **PROMOTED TO VERIFIED (general channel, all 4)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/jharkhand/ranchi.json (JH_SMART_RANCHI_* x4), sources/inventory.json. Fetched directly; names 4 real channels (online form, 24x7 phone 1800-570-1235, WhatsApp +91 8141231235, email support@smartranchi.in). Independently cross-checked against ranchi.nic.in (district government site), which separately publishes RMC's own phone number, confirming this is a real, currently-active city administration. No category-specific SLA published.]** |
| **Dehradun, Uttarakhand — 3 of 4 categories (previously 0 coverage)** | Nagar Nigam Dehradun's own Services page (nndehradun.uk.gov.in) | **PROMOTED TO VERIFIED (3 of 4 -- Roads/Potholes explicitly NOT found)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttarakhand/dehradun.json (UK_NNDDN_WASTE_SANITATION, UK_NNDDN_WATER_DRAINAGE, UK_NNDDN_STREETLIGHTS). Services page fetched and reproduced verbatim: Street Light, Drainage Complaint, Light Complaint, Sanitation, and Door to Door Waste Collection are all explicitly named items (individual sub-pages are placeholder-only, but the category names and their own department navigation entries, e.g. "Departments > Street Light Department", confirm these are real administered services). Also used the Urban Development Department's separate, dedicated solid-waste complaint portal (vlts-udd.uk.gov.in) for Waste specifically -- confirmed live and functional (reached a real mobile-OTP verification step). No Roads/Potholes item exists anywhere on the Services page (only a generic "Public Works Department" nav mention, not pursued further) -- left open rather than assumed. General contact (+91-135-2714074, nagarnigam.ddn@gmail.com) independently confirmed on the official .uk.gov.in domain, matching what third-party aggregators had separately reported.]** |

### Net result of Round 15

8 new VERIFIED records (140 -> 148; 252 -> 260 total records; 899 -> 923 chunks in the live
ChromaDB index). Goa reaches full 4/4. Jharkhand moves from 0 to full 4/4. Uttarakhand moves from
0 to 3/4 (Roads/Potholes stays open, honestly -- no source found this round that names it). 15
states remain at zero coverage (down from 17 at the start of this round).

## Round 14 (2026-08-21, session 12 continued): 2 zero-coverage states closed -- Chandigarh (4/4) and Goa (3/4)

First attempt at the 19-state "zero coverage" list (states/UTs with no knowledge-base data at
all, as opposed to a synthetic/verified ratio problem -- see
RAG_REAL_VS_SYNTHETIC_RESEARCH_PREP.md). Picked 2 candidates likely to have solid municipal web
presence (a Union Territory capital, and a tourism-economy state) rather than working the list in
order.

| Gap | Angle tried | Result | Notes |
|---|---|---|---|
| **Chandigarh (UT) — all 4 categories (previously 0 coverage)** | mcchandigarh.gov.in's own Complaints and Grievance.aspx pages | **PROMOTED TO VERIFIED (general channel, all 4)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/chandigarh/mcc_general_channel.json (CH_MCC_*_GRIEVANCE_CHANNEL x4), sources/inventory.json. Both pages fetched directly, the second time asking for verbatim raw text. Both name the same 3 real contact channels: toll-free 14420 (8 AM-8 PM), direct line 0172-2787200, and email comm-mcc-chd@nic.in. No category-specific SLA published on either page for any category -- general channel only, same quality tier as Howrah/Jodhpur's promotions.]** |
| **Goa (state) — Waste & Water (previously 0 coverage)** | goaulbservice.gov.in/Complaints.aspx -- a real, working statewide complaint form | **PROMOTED TO VERIFIED (2 of 4 categories)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/goa/statewide.json (GA_ULB_SERVICE_WASTE_SANITATION, GA_ULB_SERVICE_WATER_DRAINAGE), sources/inventory.json. Fetched directly, dropdown options reproduced verbatim: Website Related, Misconduct of Employees, Garbage Related, Illegal Construction, Choked Gutters, Dead Animals, House Tax. "Garbage Related" and "Choked Gutters" map cleanly to WASTE_SANITATION/WATER_DRAINAGE. Roads/Potholes and Streetlights are genuinely absent from this specific form's category list -- not claimed here, covered separately below/left open respectively.]** |
| **Goa (state) — Roads/Potholes (previously 0 coverage)** | cmhelpline.dpg.goa.gov.in -- Government of Goa's CM Helpline 1905 | **PROMOTED TO VERIFIED (1 category)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/goa/statewide.json (GA_CMHELPLINE_ROADS_POTHOLES). Fetched directly, named toll-free 1905 and verbatim-quoted "Infrastructure issues (roads, electricity, water supply)" plus a live example ("I need to report a pothole issue on Main Street") and a sample "Road Maintenance" resolved case. A second, explicit fetch searched the page for the words "streetlight"/"street light"/"lamp" and found zero matches -- so this source was NOT used to also claim Streetlights, even though "infrastructure"/"electricity" could plausibly be stretched to cover it. Goa's Streetlights gap remains open, honestly.]** |

### Net result of Round 14

7 new VERIFIED records (133 -> 140; 245 -> 252 total records; 878 -> 899 chunks in the live
ChromaDB index after re-running `scripts/build_rag_embeddings.py`). Chandigarh moves from 0 to
full 4-category coverage. Goa moves from 0 to 3-of-4 (Streetlights still open -- no source found
this round that verbatim supports it). 17 states remain at zero coverage (down from 19).

## Round 13 (2026-08-21, session 12 continued): 2 next-worst states targeted, 0 new records -- honest negative round

Targeted the 2 states with the next-worst verified:synthetic ratio after Round 12's Andhra Pradesh
fix: Karnataka (Bengaluru's Roads/Streetlights specifically) and Madhya Pradesh (Bhopal, 0
VERIFIED). Both gaps were already-confirmed dead ends from earlier rounds (Round 10 for both) --
this round tried only genuinely new angles, not a re-hit of anything already logged dead.

| Gap | New angle tried | Result | Notes |
|---|---|---|---|
| **Bengaluru, Karnataka — Roads & Streetlights** | BBMP's own "Sahaaya" citizen-grievance portal (bbmp.sahaaya.in / nammabengaluru.org.in), surfaced fresh via WebSearch, not a domain tried in Rounds 4/9/10; BESCOM (Bangalore Electricity Supply Co.) as a genuinely different department for streetlights specifically | **CONFIRMED DEAD END (both new angles exhausted)** | **[CHECKED — bbmp.sahaaya.in's TLS certificate doesn't match its own hostname (cert is for *.nammabengaluru.org.in), and following that redirect lands on a bare client-rendered shell with no fetchable content (same SPA-shell failure class as bbmp.gov.in-family domains in earlier rounds). BESCOM was checked as a genuinely different angle since streetlights could plausibly be an electricity-company matter, not a municipal one -- but BESCOM's own official account, responding to a citizen, states streetlight issues are BBMP's remit, not BESCOM's ("street light issues pertains to BBMP please contact the BBMP customer care number 080-22660000/22221188"), routing back to BBMP rather than opening a new department. That phone number could not be independently confirmed on a fetchable BBMP-family webpage (bbmp.gov.in/en/contact-us returns HTTP 404, confirmed via direct curl, not just WebFetch) -- the project's schema has no source_type for a social-media post, so it wasn't used as a citable source per the established evidentiary bar. Bengaluru Roads/Streetlights remains open; this is now the 4th round (4, 9, 10, 13) to confirm every angle tried is exhausted.]** |
| **Bhopal, Madhya Pradesh — all 4 categories (still 0 VERIFIED)** | Madhya Pradesh's Directorate of Urban Administration & Development's own GIS/geoportal presence (geoportal.mp.gov.in/UADD_New/), surfaced fresh via a targeted site-scoped search, genuinely different from smartbhopal.city/mpenagarpalika.gov.in/bhopal.nic.in (all confirmed dead in Round 10) | **CONFIRMED DEAD END (this round's angle)** | **[CHECKED — geoportal.mp.gov.in's root domain is live (HTTP 200, confirmed via direct curl), but the specific UADD_New path returns HTTP 404 -- a stale/incorrect path, not a working page. mpenagarpalika.gov.in was independently re-checked via direct curl this round (not just re-trusting Round 10's finding) and is still unreachable (connection timeout), confirming it's a durable outage, not a one-time fluke. A toll-free number (18002335522) and Bhopal address for the Directorate surfaced via WebSearch, but only through third-party aggregator sites (complainthub.org-style pages), never confirmed on an actual official .gov.in page reachable by direct fetch -- per the project's standing rule against citing anything not independently checked, this was not used to build a record. Bhopal remains at 0 VERIFIED, still exhausted per Round 10 plus this round's new angle.]** |

### Net result of Round 13

0 new VERIFIED records (133 unchanged, 245 total records unchanged). Both gaps remain open, each
now backed by a 4th (Bengaluru) or 2nd (Bhopal, new-angle) round of genuinely different attempts,
not just repeated guesses. Per the project's honesty rule, a negative round is still logged in
full -- no record was forced to close these gaps, and the accumulated close-but-not-quite leads
(BESCOM's phone number, the toll-free DUAD number) are recorded here for whoever picks this up
next, rather than silently dropped.

## Round 12 (2026-08-21, session 12): Andhra Pradesh's worst-ratio state, 1 new state-wide general channel found

Targeted Andhra Pradesh specifically (the state with the worst verified:synthetic ratio, 4V+8S
across all 4 categories -- see DATA_COVERAGE_TRACKER.md). Vijayawada (0 VERIFIED, previously
confirmed a fully exhausted dead end in Round 11) and Visakhapatnam (4 VERIFIED already, via GVMC)
were the 2 cities in scope.

| Gap | Angle tried | Result | Notes |
|---|---|---|---|
| **Andhra Pradesh — all 4 categories (state-wide)** | cdma.ap.gov.in (the Commissioner & Director of Municipal Administration's own state portal) -- a genuinely different domain from every Vijayawada-specific angle tried in Round 11 | **PROMOTED TO VERIFIED (general channel, all 4, state-wide)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/andhra_pradesh/cdma_statewide.json (AP_CDMA_STATEWIDE_GRIEVANCE_* x4), sources/inventory.json. cdma.ap.gov.in/services/grievances/ is real and live; fetched twice independently (once for a summary, once explicitly asking for verbatim raw text with no paraphrasing) to guard against a misread, per the project's own standing caution about not overclaiming -- both fetches agree exactly. The page names 5 grievance categories with sub-items (Sanitation: Garbage Not Collected/Street Cleaning/Public Toilet Maintenance; Water Supply: Disruption/Leakage/Quality; Roads: Potholes/Road Damage/Speed Breakers; Parks & Greenery -- not one of ours; Street Lighting: Not Working/Electrical Hazards/New Installation) and states submission is via "the Puramithra App or Citizen Portal". No numeric SLA: the page's own sibling "Grievance SLAs" sub-page (cdma.ap.gov.in/resources/grievance-sla/) was checked directly and returned "Showing 0 of 0 SLA items" -- a genuinely empty results table, not a fetch failure. 3 documents linked from a third CDMA page (others/portal-info/citizen-charter/) -- G.O Ms.No.198, the Citizen's Charter .doc, and the Puraseva Centre User Manual -- were each checked directly via curl (not just WebFetch) and are dead links (HTTP 404, 400, 404 respectively). The ULB Web Directory page (cdma.ap.gov.in/ulb-web-directory) was also checked for a direct VMC link -- it's a client-side/AJAX-loaded shell ("Loading ULB data...") that static fetching can't populate, the same SPA-shell failure class documented for other AP/Karnataka domains in earlier rounds. Recorded as city=null/geographic_scope=STATE (NOT attributed to Vijayawada specifically) since the source page itself makes no Vijayawada-specific claim -- applies generally to every ULB under CDMA. Vijayawada's own city-specific 0-VERIFIED gap is therefore still open; this closes a different, real gap (AP had no state-wide general channel at all before this).]** |

### Net result of Round 12

4 new VERIFIED records (129 -> 133; 241 -> 245 total records; 866 -> 878 chunks in the live
ChromaDB index after re-running `scripts/build_rag_embeddings.py`). Andhra Pradesh's ratio
improves from 4V+8S to 8V+8S across the state. Vijayawada's own city-specific gap (0 VERIFIED)
remains open and is still a confirmed dead end per Round 11 -- closing it specifically would need
a genuinely new angle beyond cdma.ap.gov.in (already used here) and everything tried in Round 11.

## Round 11 (2026-08-16, session 11): 3 zero-coverage cities from early rounds, 2 closed via general channels

Targeted 3 cities with 0 VERIFIED records each, last touched in rounds 2/3 before later rounds'
site-navigation/PDF-mining techniques existed. Each had one specific untried lead. 2 of 3 closed
(via general, non-category-specific channels); 1 stayed fully dead.

| City | Angle tried | Result | Notes |
|---|---|---|---|
| **Howrah, West Bengal — all 4 categories** | howrah.gov.in (district govt domain, separate from blocked myhmc.in) | **PROMOTED TO VERIFIED (general channel, all 4)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/west_bengal/howrah.json (WB_HMC_*_GRIEVANCE_CHANNEL x4), sources/inventory.json. myhmc.in confirmed 403-blocked at the whole-domain level again this round -- including a specific static PDF asset under wp-content/uploads (Water Regulations 2007), proving the block isn't limited to dynamic/bot-detected pages. howrah.gov.in/service/hmc-related-services/ was fetched and followed through to every linked HMC service; all dynamic services (grievance submission, complaint status, trade license) point back to the blocked myhmc.in. However, howrah.gov.in's own Public Utilities listing (a genuinely different page on the same working domain) publishes HMC's real head-office address, phone (03326383211), and email (citizen.care@myhmc.in) directly. howrah.gov.in's own "Solid Waste Management" page is a bare "Coming Soon" placeholder, and its "Citizen Charter" document category lists no HMC-specific document (only unrelated state documents). A WebSearch for a primary-sourced HMC citizen charter PDF found only the HMC Act 2019 (legal framework text, hosted on the blocked myhmc.in domain, no service SLA) and a Scribd mirror (third-party redistribution, disqualified per project rules). Closes Howrah's 0-coverage gap via the same general-channel pattern as Lucknow/Gaya/Gurugram/Warangal/Mysuru.]** |
| **Jodhpur, Rajasthan — all 4 categories** | Rajasthan's statewide LSG portal (lsg.urban.rajasthan.gov.in), following the pattern that produced Jaipur's rich charter | **PROMOTED TO VERIFIED (general channel, all 4)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/rajasthan/jodhpur.json (RJ_JMC_*_GRIEVANCE_CHANNEL x4), sources/inventory.json. jodhpurmc.org (confirmed dead in a prior round) was not retried. The LSG portal's ULB-Jodhpur sub-site is real and reachable (unlike jodhpurmc.org) and names a real Commissioner (Hari Singh Rathore) as nodal officer with direct phone (0291-2651464) and email. However, unlike Bikaner's citizen-charter URL pattern that inspired this angle, Jodhpur's own Citizen Charter, Feedback/Grievances, and Nagar Palika Telephone List pages were all fetched and -- confirmed via raw HTML inspection, not just WebFetch's rendering -- are genuinely unpopulated content templates (site last updated 28/03/2016), each showing only the same repeated nodal-officer contact box with zero service table, SLA, or category breakdown. A separate portal, urban.rajasthan.gov.in/mcjs (also surfaced via WebSearch as "Jodhpur Municipal Corporation's portal"), is a bare, contentless page fragment. Jodhpur Development Authority (jdajodhpur.org) was checked per instruction as a distinct authority once the municipal-corporation angle went dead -- its own domain has an EXPIRED TLS certificate (a new, distinct failure mode from every TLS error logged in prior rounds: this is a genuine certificate-expiry misconfiguration, not a cert-chain/trust-store issue), and separately, JDA's statutory remit (land/planning/building permissions) doesn't cover routine civic complaints in the first place, so it wasn't pursued further even via the `-k`-flag workaround. Closes Jodhpur's 0-coverage gap via the same general-channel pattern as Howrah this round.]** |
| **Vijayawada, Andhra Pradesh — all 4 categories (still 0 VERIFIED)** | services.india.gov.in (national government services portal) — 2 specific already-logged URLs | **CONFIRMED DEAD END (lead retired)** | **[CHECKED — both named URLs (the VMC Commissioner grievance page and the sibling "check status of complaints" page) return HTTP 302 and redirect identically to the generic `www.india.gov.in/services` landing page, confirmed independently via both WebFetch and a direct curl fetch (ruling out a tool-specific quirk). A further WebSearch surfaced 2 more services.india.gov.in detail-page URLs for VMC (an "online complaint reminder" page and a "write back to Commissioner" page) that were not part of the original 2-URL lead -- not fetched individually since the identical retirement pattern across the first 2 confirmed the entire `service/detail/...` URL structure for this portal has been deprecated site-wide, not just for these 2 specific pages. Combined with vijayawada.cdma.ap.gov.in (client-rendered shell, confirmed dead in an earlier round) and ourvmc.org (connection-refused, no TLS listener, confirmed dead in an earlier round), all 3 plausible angles for Vijayawada are now exhausted. Vijayawada remains 0 VERIFIED records.]** |

### Net result of Round 11

8 new VERIFIED records (121 -> 129). Howrah and Jodhpur both moved from 0 to full 4-category
coverage, though both via general (SLA-not-found) channels rather than department-specific
contacts -- the same quality tier as Lucknow's round-8 outcome, an honest reflection of what these
2 cities' reachable primary sources actually contain. Vijayawada remains the project's one fully
confirmed zero-coverage city with every plausible official-source angle now exhausted (client-
rendered shells, connection-refused domains, and a retired national-portal URL structure).

## Round 10 (2026-08-15, session 10): 3 targeted gaps, 0 new records -- a fully honest negative round

All 3 targeted angles were run down to a specific, confirmed conclusion. None produced a new
VERIFIED record. Logged in full per the project's honesty rule -- an empty round is still a
useful result when every angle is genuinely exhausted, not abandoned early.

| Gap | Angle tried | Result | Notes |
|---|---|---|---|
| **Bhopal, Madhya Pradesh — all 4 categories (still 0 VERIFIED)** | Smart City Bhopal's own domain (Indore precedent) | **CONFIRMED DEAD END (this round's angle)** | **[CHECKED — searched for the real Smart City Bhopal domain rather than guessing. `smartbhopal.city` (found via WebSearch, described as "the official Smart City Bhopal website") returns HTTP 200 but its HTML body is a 114-byte JS-only redirect to `/lander`, which itself resolves to a GoDaddy domain-parking shell (`window.LANDER_SYSTEM="PW"`, `img1.wsimg.com/parking-lander` assets) -- this domain has expired and been resold/parked, it is NOT a live Smart City Bhopal presence, a genuinely different and more specific failure than the empty-client-shell class already documented for bmconline.gov.in. The user's own suggested alternate, `smartcitybhopal.gov.in`, does not even resolve via DNS (`Could not resolve host`) -- not a real domain. `mpenagarpalika.gov.in` (MP's statewide e-Nagar Palika portal, surfaced via WebSearch) resolves but the connection is refused/times out on port 443 from 2 independent network paths (this environment's curl and WebFetch's own fetcher both failed identically) -- a genuine infrastructure-level unreachability, not a TLS-cert issue. `bhopal.nic.in`'s own "How to lodge a Grievance" page (the district government site, a different domain again) was checked and only redirects to `cmhelpline.mp.gov.in` -- the exact same statewide CM Helpline source already used for MP_CMHELPLINE_GENERAL_GRIEVANCE, not new. A real "Bhopal Plus" platform with grievance-redressal features was found via WebSearch but is app-store-only (15,000+ downloads, no confirmed website URL) -- not fetchable as a primary web source. Bhopal remains 0 VERIFIED records; every domain angle plausibly reachable this round is now exhausted.]** |
| **New Delhi (NDMC) — Streetlights (still missing; 3/4 categories done)** | Re-verify DL_NDMC_COMPLAINTS_PAGE's actual content before building a reused-channel record | **NOT PROMOTED -- claim did not hold up on re-check** | **[CHECKED — re-fetched ndmc.gov.in/complaints.aspx and asked explicitly whether streetlight/electrical-lighting complaints are named as a handled category, with a request for verbatim quotes. Result: the page does NOT explicitly mention streetlights, street lighting, electrical lighting, or lamp posts anywhere -- it lists general complaint channels (1533 toll-free, WhatsApp, NDMC 311 app, Suvidha Camp, dashboard) with no itemized category list at all, only unrelated billing categories (Electricity, Water, Property, Estate, Baratghar) in a bill-payment section. The existing DL_NDMC_GENERAL_GRIEVANCE_CHANNEL record's own service_name ("General -- Waste/Streetlights") and description text assert streetlight coverage, but this appears to be an overreach not supported by the source page's actual content -- flagging this here rather than silently correcting the existing record, since fixing it wasn't this round's task. Per explicit instruction, since the streetlight mention could not be verbatim-confirmed, NO new record was built. NDMC's Streetlights gap remains open; SLA.aspx's 41-row table (confirmed zero streetlight line item in Round 7) still stands as the other checked source for this city.]** |
| **Bengaluru, Karnataka — Roads & Streetlights** | Sakala (sakala.kar.nic.in) -- Karnataka's separate statutory right-to-service portal | **CONFIRMED DEAD END (all plausible angles now exhausted)** | **[CHECKED — sakala.kar.nic.in is real and reachable (HTTP 200), unlike every karnataka.gov.in/bbmp.gov.in-family domain tried in Rounds 3-4 and 9 -- confirming it sits on genuinely different infrastructure. Its department listing page (department_kan.aspx) contains a real JS-driven toggle section for BBMP ("bruhat") but the section's actual content loads via client-side AJAX/postback that static fetching can't execute -- consistent with the SPA-shell failure class seen elsewhere. Fell back to Sakala's own 500-page, ~22MB "Notified Services" compendium PDF (a fully static primary document, sidestepping the AJAX problem) and searched its extracted text programmatically rather than reading it by hand: the Latin-script string "BBMP" appears on exactly 1 of 500 pages, in a BDA (Bangalore Development Authority) property/khata-transfer service item, entirely unrelated to road or streetlight complaints; Kannada-script searches for ಬಿಬಿಎಂಪಿ (BBMP), ಬೃಹತ್ (Bruhat), and ಮಹಾನಗರ ಪಾಲಿಕೆ (Municipal Corporation) combined with ರಸ್ತೆ (road) and ಬೀದಿ ದೀಪ (streetlight) returned zero matches for "road" or "streetlight" anywhere in the entire document. This is consistent with Sakala's actual statutory scope (the Karnataka Sakala Services Act, 2011 covers time-bound *applications* for certificates/licenses/permits -- a fundamentally different government-interaction category from *complaint/grievance redressal* for civic infrastructure like potholes or streetlights), not an extraction failure. Per explicit instruction, this confirms Bengaluru Roads/Streetlights as a genuine dead end across all plausible angles (5+ karnataka.gov.in/bbmp.gov.in domains, CPGRAMS, data.gov.in, and now Sakala) -- no further angle should be attempted without a fundamentally new lead, not another domain guess.]** |

### Net result of Round 10

0 new VERIFIED records (121 unchanged, 233 total records, 842 chunks unchanged). All 3 gaps
remain open, each now backed by a thorough, specific, honestly-logged negative result rather than
an untried angle. This is a legitimate outcome of the project's honesty rule -- no record was
forced, no fact was invented, and no existing record was silently "fixed" to match a task that
wasn't asked for (the NDMC general-channel record's overreaching Streetlights language is flagged
above for future attention, not corrected here).

## Round 9 (2026-08-15, session 9): retrying 4 confirmed dead ends via genuinely new angles

Explicit retry of 4 category gaps confirmed dead in prior rounds -- but only via new angles, not
by re-hitting domains already confirmed blocked. 2 of 4 closed; 2 confirmed dead again, this time
via exhausted new angles rather than un-tried ones.

| Gap | New angle tried | Result | Notes |
|---|---|---|---|
| **Bengaluru, Karnataka — Roads & Streetlights** | CPGRAMS (pgportal.gov.in) department registry; data.gov.in open datasets | **CONFIRMED DEAD END (new angle exhausted)** | **[CHECKED — pgportal.gov.in's homepage returns HTTP 200 but is a client-side-rendered Angular SPA shell with no static department/ministry directory reachable via direct fetch (same failure class as imcindore.mp.gov.in and bmconline.gov.in from earlier rounds) -- no way to confirm or deny whether BBMP/GBA is a registered receiving department without JS execution, which this tooling can't do. data.gov.in was reachable (HTTP 200, unlike the earlier 403 from WebFetch's default UA) and searched directly via 4 queries ("BBMP", "Bruhat Bengaluru", "Karnataka municipal grievance", "Bengaluru streetlight") -- all 4 returned "No Result Found", confirming no relevant open dataset exists. Karnataka's Roads/Streetlights gap remains open; both new angles this round are now exhausted, not just untried, alongside the 5 domain-block angles from Round 4.]** |
| **Faridabad, Haryana — Water/Drainage** | PHED Haryana's own Field Office Telephone Directory (a real, separate state department from MCF) | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/haryana/faridabad.json (HR_PHED_FARIDABAD_WATER_CONTACT), sources/inventory.json. Re-fetching ulbharyana.gov.in/Faridabad/404's Officers Directory confirmed the JE(HKRNL) entries are listed under the general Engineering Branch with no explicit water/PHED label in the page text itself (HKRNL = Haryana Kaushal Rozgar Nigam Limited, a contract-staffing body, not water-specific) -- correctly left unused again, consistent with Round 8's reasoning. The real new angle: phedharyana.gov.in (Haryana's actual Public Health Engineering Department, a genuinely different body from mcfaridabad.com/.in) exists and works; its homepage links to a "Field Office (SE & EE) Directory" which turned out to be a server-generated PDF (not HTML) at Telephone_Dir/TelDir.aspx?id=w -- read via the established Read-tool-on-saved-PDF workaround. Names a real Executive Engineer, "Faridabad PHED No. 1", Sh. Rahul Berwal, with a direct mobile and official email. Closes Faridabad's last missing category -- the city now has all 4.]** |
| **Nagpur, Maharashtra — Roads/Potholes** | NMC's own site-map page (nmcnagpur.gov.in/site-map), not further URL guessing | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/maharashtra/nagpur.json (MH_NMC_PUBLIC_WORKS_ROADS_CONTACTS), sources/inventory.json. nagpur.gov.in (the district site, a different domain) was checked and confirmed to have no PWD/Roads contact page -- genuinely exhausted. The real new angle: rather than guessing more URL patterns, fetched nmcnagpur.gov.in's actual site-map page directly (found via raw HTML href scan of the homepage, not visible through WebFetch's markdown conversion) and parsed real link text/href pairs. Found "Public Work Department" -> /public-work-department (singular "Work", "-department" suffix -- a pattern distinct from all 6 guesses that 404'd last round) and "Hot Mix Plant Department" -> /hot-mix-plant-department (also a new, correct pattern vs. last round's bare /hot-mix-plant guess). Both pages are real and load, naming real Chief/Executive/Deputy/Junior Engineers. Closes Nagpur's last missing category -- the city now has all 4.]** |
| **Patna, Bihar — Streetlights** | PMC's Office Directory re-examined in full; CircleOfficials.aspx and PMCOfficials.aspx (2 other officer-listing pages) also checked; /electrical-department and /electrical-department.aspx guessed | **CONFIRMED DEAD END (new angles exhausted)** | **[CHECKED — pmc.bihar.gov.in's homepage HTML was scanned directly (not via WebFetch's markdown summary) for every department/aspx link; the only Electrical-related link anywhere on the site, on any of the 3 officer-listing pages checked (office-directory.aspx, CircleOfficials.aspx, PMCOfficials.aspx), is the identical "Installation of Street Lights" link pointing to the already-confirmed-dead MIS Electric.pdf (pure installation-count table, no complaint content, per Round 8's finding). No dedicated Electrical Department page exists at any guessed URL (/electrical-department -> 404, /electrical-department.aspx -> 404, unlike Nagpur's working "-department"-suffix pattern). This is now a thoroughly exhausted dead end -- 3 separate officer-listing pages plus 2 URL guesses, not a single un-tried page remains on pmc.bihar.gov.in's own site. Patna's Streetlights gap remains open.]** |

### Net result of Round 9

2 new VERIFIED records (119 -> 121). Faridabad and Nagpur both now have full 4-category coverage
(their last remaining gaps closed via genuinely different real sources/pages than the ones
confirmed dead in Round 8). Bengaluru (Roads/Streetlights) and Patna (Streetlights) remain open --
both were retried via 2 genuinely new angles each this round, and both angles are now confirmed
exhausted (not just untried), same honest-dead-end standard applied throughout this project. Any
further attempt on these 2 gaps would need a genuinely new angle not yet tried, not a re-hit of
CPGRAMS/data.gov.in or pmc.bihar.gov.in's own officer-listing pages.

## Round 8 (2026-08-15, session 8): 4 targeted cities, 12 category gaps, 3 confirmed dead ends

Targeted exactly 4 cities' explicitly-untried category gaps (Patna, Faridabad, Nagpur, Lucknow),
per explicit instruction to skip Bengaluru Roads/Streetlights and NDMC Streetlights (both already
confirmed durable dead ends in prior rounds) and to NOT recategorize Patna's/Lucknow's existing
Waste-specific records. 9 new VERIFIED records closing 9 of 12 targeted gaps; 3 confirmed dead ends
logged below with specific technical reasons.

| Gap | Result | Notes |
|---|---|---|
| **Patna, Bihar — Water/Drainage** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/bihar/patna.json (BR_PMC_WATER_SUPPLY_CONTACTS), sources/inventory.json. PMC's own Water Board Details page (pmc.bihar.gov.in/SWMWaterSupply.aspx, a different page from the existing Citizen Charter used for the untouched Waste record) lists 4 real, named circle-wise Pipeline Inspectors with direct mobile numbers, covering Patliputra/New Capital, Kankarbagh, Bankipur, and Patna City/Azimabad circles. No numeric SLA published.]** |
| **Patna, Bihar — Roads/Potholes** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/bihar/patna.json (BR_PMC_ENGINEERING_ROADS_CONTACTS), sources/inventory.json. PMC's own Office Directory page (office-directory.aspx) names the real Chief Municipal Engineer and an Executive Engineer (Civil) with direct mobile numbers. No numeric SLA published.]** |
| **Patna, Bihar — Streetlights** | **CONFIRMED DEAD END** | **[CHECKED — PMC's "Installation of Street Lights" link resolves to assets/pdf/MIS Electric.pdf. WebFetch's own extraction returned garbled binary/stream data; worked around via the Read-tool-on-saved-PDF pattern, which rendered the full document. Confirmed: this is purely a ward-wise streetlight INSTALLATION COUNT table (81,504 total citywide), an Excel-exported MIS report with zero complaint mechanism, contact info, or SLA content — a genuine content mismatch, not a fetch failure. No dedicated Electrical/Street Light department page or officer was found elsewhere on pmc.bihar.gov.in. Patna's Streetlights category remains open.]** |
| **Faridabad, Haryana — Roads/Potholes** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/haryana/faridabad.json (HR_MCF_ENGINEERING_ROADS_CONTACTS), sources/inventory.json. Haryana's own ULB portal (ulbharyana.gov.in/Faridabad/404 — genuinely a different domain from mcfaridabad.com, MCF's own site) publishes a rich, real Officers Directory including the Engineering Branch: Chief Engineer, Superintending Engineer, and multiple Executive/Assistant/Junior Engineers, each with direct mobile numbers. No numeric SLA published.]** |
| **Faridabad, Haryana — Streetlights** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/haryana/faridabad.json (HR_MCF_ELECTRICAL_STREETLIGHT_CONTACT), sources/inventory.json. Same ulbharyana.gov.in Officers Directory names a real Junior Engineer (Electrical) under the Planning Branch, with a direct mobile number. No numeric SLA published.]** |
| **Faridabad, Haryana — Water/Drainage** | **CONFIRMED DEAD END (systemic TLS block, new domain)** | **[CHECKED — mcfaridabad.com/contact and mcfaridabad.com/faridabad-water-supply-sewerage-services/ both failed with "unable to verify the first certificate", and mcfaridabad.in/ (the alternate TLD) returned HTTP 403 Forbidden. This confirms mcfaridabad.com is an entirely new, separate TLS-blocked domain (distinct from the already-documented karnataka.gov.in/bbmp.gov.in and ahmedabadcity.gov.in blocks) — the SWM Bye-laws PDF previously used from this domain family was pure regulatory content, not reusable, and no alternate real source for Water was found via ulbharyana.gov.in's Officers Directory either (the many "JE(HKRNL)" entries under Engineering are ambiguous contract-staff designations, not clearly water-specific, so deliberately not used to avoid misattribution). Faridabad's Water/Drainage category remains open.]** |
| **Nagpur, Maharashtra — Waste/Sanitation** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/maharashtra/nagpur.json (MH_NMC_SOLID_WASTE_CONTACTS), sources/inventory.json. nmcnagpur.gov.in/solid-waste-management (URL guessed from the pattern that later also worked for /electrical-department) names the real Deputy Commissioner (SWM) and Chief Sanitary Officer with direct mobile numbers; supplemented with all 10 zone-wise Chief Sanitary Officers from the separate /zonal-officers page (explicitly labeled sanitation-specific, not general zone contacts). No numeric SLA published.]** |
| **Nagpur, Maharashtra — Streetlights** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/maharashtra/nagpur.json (MH_NMC_ELECTRICAL_STREETLIGHT_CONTACT), sources/inventory.json. nmcnagpur.gov.in/electrical-department names the real Executive Engineer (Electrical) with a direct mobile number and department line. No numeric SLA published.]** |
| **Nagpur, Maharashtra — Roads/Potholes** | **CONFIRMED DEAD END** | **[CHECKED — 6 URL-pattern guesses against nmcnagpur.gov.in (/public-works, /public-works-department, /engineering-department, /roads-department, /public-health-engineering, /hot-mix-plant) all returned HTTP 404, despite the identical guessing strategy succeeding for 2 sibling departments (/electrical-department, /solid-waste-management) on the same domain this same round. NMC's homepage nav confirms a "Public Works" department exists by name, but its actual URL slug could not be determined. The public grievance system (/grievance/complaint_form.php) requires OTP entry and the grievance-redressal login (/grievance/) requires username/password — both gated behind authentication with no public category list visible, confirmed dead ends for extracting data without credentials. Nagpur's Roads/Potholes category remains open.]** |
| **Lucknow, Uttar Pradesh — Roads/Potholes** | **PROMOTED TO VERIFIED (general channel)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttar_pradesh/lucknow.json (UP_LMC_ROADS_POTHOLES_GRIEVANCE_CHANNEL), sources/inventory.json. lmc.up.nic.in/helpline.aspx (a different page from the existing, untouched, genuinely waste-specific garbage-collection helpline record) publishes a real toll-free Control Room number (1533) and WhatsApp/calling lines, general-purpose rather than category-routed. No dedicated Roads/Engineering department page or officer directory was found on lmc.up.nic.in's nav. No numeric SLA published for any category on this page.]** |
| **Lucknow, Uttar Pradesh — Water/Drainage** | **PROMOTED TO VERIFIED (general channel)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttar_pradesh/lucknow.json (UP_LMC_WATER_DRAINAGE_GRIEVANCE_CHANNEL), sources/inventory.json. Same lmc.up.nic.in/helpline.aspx general channel. Lucknow Jal Sansthan's own domain (jklmc.gov.in) failed with "unable to get local issuer certificate" — a genuine TLS failure, not missing content; not retried per the established one-attempt-on-clear-TLS-error pattern. No numeric SLA published.]** |
| **Lucknow, Uttar Pradesh — Streetlights** | **PROMOTED TO VERIFIED (general channel)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttar_pradesh/lucknow.json (UP_LMC_STREETLIGHTS_GRIEVANCE_CHANNEL), sources/inventory.json. Same lmc.up.nic.in/helpline.aspx general channel. No dedicated Electrical department page or officer directory found on lmc.up.nic.in's nav. lmc.up.nic.in/complaint.aspx and /Complaint/Complaint.aspx (which a WebSearch summary claims offers per-category complaint routing) both hit an infinite redirect loop ("Too many redirects, exceeded 10") on direct fetch — this could not be independently verified and is NOT used as a source. No numeric SLA published.]** |

### Net result of Round 8

9 new VERIFIED records (110 → 119). Patna and Faridabad each closed 2 of their 3 targeted gaps
with real, named-officer, department-specific contacts (Streetlights and Water respectively
confirmed as genuine dead ends — a content mismatch for Patna, a systemic TLS block for
Faridabad). Nagpur closed 2 of its 3 targeted gaps the same way (Roads confirmed dead end after
6 URL-pattern guesses all 404'd, despite the identical strategy working for its 2 siblings).
Lucknow closed all 3 targeted gaps, but only via a general, non-category-specific Control Room
channel (1533) — the same quality tier as Round 7's Gaya/Gurugram/Warangal/Mysuru
re-categorizations, not department-specific contacts like the other 3 cities this round. No
existing records were touched or recategorized (Patna's and Lucknow's genuinely waste-specific
records were left exactly as they were, per explicit instruction).

## Round 7 (2026-08-15, session 7): audit + re-categorization + 4 targeted new fetches

Two-part round. Part 1 was a pure audit/re-categorization pass (no new research) fixing a
schema-driven retrieval gap: several already-verified general-purpose channels were filed under
WASTE_SANITATION only (one category per record), so they never surfaced for Roads/Water/
Streetlights queries even though their own descriptions already say they're category-agnostic.
Part 2 was 4 small, targeted new fetches. 17 new VERIFIED records total.

### Part 1 — re-categorization (audit, every source re-fetched to double-check nothing was missed)

| City | Source re-fetched | Result |
|---|---|---|
| Gaya, Bihar | gayamunicipal.net | **[CONFIRMED GENERAL — added ROADS_POTHOLES, WATER_DRAINAGE, STREETLIGHTS variants of BR_GMC_GENERAL_GRIEVANCE_CHANNEL, same source. Re-fetch confirmed only the single Feedback/Complain link and Waste-search tools, no category breakdown.]** |
| Gurugram, Haryana | services.gmda.gov.in | **[CONFIRMED GENERAL — added the same 3 category variants of HR_GMDA_GENERAL_GRIEVANCE. Re-fetch confirmed only Register/Status/Callback/Old-Age-Services options, no category breakdown.]** |
| Warangal, Telangana | gwmc.gov.in | **[CONFIRMED GENERAL — added the same 3 category variants of TS_GWMC_GENERAL_GRIEVANCE_CHANNEL. Re-fetch confirmed only a general helpline number and Grievance Registration/Report links.]** |
| Mysuru, Karnataka | mysore.nic.in MCC page | **[CONFIRMED GENERAL — added the same 3 category variants of KA_MCC_GENERAL_GRIEVANCE_CHANNEL. Re-fetch confirmed only Commissioner's Office phone/email/address.]** |
| Indore, Madhya Pradesh | smartcityindore.org/grievance-registration/ | **[CONFIRMED — re-fetch listed all 9 categories verbatim in order (Garbage, Road & Footpath, Public Toilets/Urinals, Park & Play Ground, Water Supply, Sewerage/Drainage, Public Health, Street Light, Other); "Garbage" is genuinely the first category and had no record built from it despite the other 3 (Water/Roads/Streetlights) already existing. Added MP_IMC_SMARTCITY_WASTE_CHANNEL, same source.]** |

13 new records from Part 1 (3×4 cities + 1 Indore), all citing already-existing SourceRecord entries — no new inventory.json entries needed for this part.

### Part 2 — new targeted fetches

| Gap | Result | Notes |
|---|---|---|
| **New Delhi (NDMC) — Streetlights** | **CONFIRMED SLA NOT FOUND (0 new records)** | **[CHECKED — re-fetched ndmc.gov.in/SLA.aspx and extracted all 41 line items verbatim (Health/Welfare/Architect/Electric/Water/Estate/Civil Engineering/Enforcement/Horticulture/Accounts departments). Genuinely zero streetlight/electrical-lighting/lamp-post line item exists in this table — confirmed absent, not missed. This is an honest source limitation, not a fetch failure; no record built.]** |
| **Kolkata, West Bengal — Roads/Potholes** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/west_bengal/kolkata.json (WB_KMC_ROADS_GRIEVANCE_CHANNEL), sources/inventory.json. KMC's homepage navigation led to Engineering (Civil) → Manholes.jsp (real pothole-repair procedure: "Inform the local Ward Office/Borough Office/KMC Control Room") and then KMC's Common Complaint e-Form (ComplaintFormAction.do), which lists "Repair of potholes in Roads / Footpath and related" as category code 12 of 19. No numeric SLA or direct KMC Control Room phone number found on either official page — a number surfaced only via a third-party aggregator, not used. Closes Kolkata's last missing category.]** |
| **Chennai, Tamil Nadu — Water & Drainage** | **PROMOTED TO VERIFIED (rich)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/tamil_nadu/chennai.json (TN_CMWSSB_WATER_SEWERAGE_SLA), sources/inventory.json. cmwssb.tn.gov.in (Chennai Metrowater's own domain, distinct from chennaicorporation.gov.in already used for GCC's 3 contact records) — after checking /complaint-redressal and /complaints-grievance (both real, giving the 24x7 Complaint Cell number and a genuine 3-minute internal-SMS-handoff commitment but no SLA table), the /citizencharter page gave a rich, detailed, real numeric SLA table across 13 water/sewerage service types (2 to 20 days depending on category) — comparable in quality to Jaipur's charter, the project's previous richest single source. Closes Chennai's last missing category.]** |
| **Thiruvananthapuram, Kerala — Roads & Waste** | **PROMOTED TO VERIFIED (2 records)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/kerala/thiruvananthapuram.json (KL_TVM_ROADS_CHANNEL, KL_TVM_WASTE_CHANNEL), sources/inventory.json. A different page from smarttvm.tmc.lsgkerala.gov.in's root (already used for Water/Septage and Streetlights) — /complaint/report is a live complaint-tracking dashboard showing real filed complaints (e.g. an actual logged "Road pothhole" complaint, ref REV42348) confirming a "General Complaints[Rev]" category covers roads, and 3 named waste categories (Waste Related Complaints, Waste Littering, Spot the Dump). Both share the single general municipal number (9496434488) rather than a dedicated line like Water/Septage or Streetlights have. tmc.lsgkerala.gov.in/en/page/33 (a Solid Waste Management informational page) was also checked but returned only nav/footer content. Closes Thiruvananthapuram's last 2 missing categories — the city now has all 4 categories covered.]** |

### Net result of Round 7

17 new VERIFIED records (93 → 110). Part 1 was zero-fetch-risk (pure re-categorization of
already-verified content) and closed 13 category gaps across 5 cities. Part 2 closed 3 of 4
targeted gaps (Kolkata, Chennai, Thiruvananthapuram all now have full 4-category coverage);
NDMC Streetlights was confirmed as a genuine source limitation (SLA table exists but doesn't
cover this category), not a research gap.

## Round 6 (2026-08-14, session 6, FINAL ROUND): 4 targeted gaps

Final round, scoped to exactly 4 remaining gaps rather than a broad sweep. 3 of 4 produced new
VERIFIED records; 1 (Bengaluru) was confirmed exhausted after 3 more genuinely-different-domain
attempts on top of the 2 prior rounds' worth of TLS-blocked domains.

| Gap | Result | Notes |
|---|---|---|
| **Delhi — MCD Streetlights** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/delhi/delhi.json (DL_MCD_STREETLIGHT_GRIEVANCE_CHANNEL), sources/inventory.json. Searched for MCD's own Street Light Board handbook (the document family that yielded the Round 3 toilet-SLA record) — not found via 2 targeted WebSearches. Directly fetched MCD's Citizen Charter (mcdonline.nic.in/portal/citizenCharter — confirmed to cover ONLY licensing/registration services: trade licenses, birth/death certs, property tax, construction permits, zero infrastructure-complaint content), Contact Us, and RTI pages — all three confirm the same general Citizen's Call Center (155305) / MCD311 app channel already recorded for Waste, but no streetlight-specific SLA anywhere. Built a new, correctly-categorized STREETLIGHTS record using this same real, thrice-confirmed channel — same pattern already used for NDMC and other cities' general channels.]** |
| **Bengaluru — Roads & Streetlights** | **CONFIRMED EXHAUSTED (0 new records)** | **[CHECKED — 3 genuinely different, non-karnataka.gov.in/non-bbmp.gov.in domains tried this round, all dead: (1) `sahaaya2.bbmpgov.in` — TLS cert altname mismatch, cert covers `*.bbmp.gov.in`, confirming this "new" domain shares the SAME blocked underlying infrastructure, not a genuinely separate one; (2) `vigeyegpms.in/bbmp/...` (a third-party vigilance/grievance platform) — ECONNREFUSED; (3) `bengaluruurban.nic.in/en/service/public-grievances/` (a working nic.in domain) — loads fine but only redirects to the state-level `ipgrs.karnataka.gov.in` portal (karnataka.gov.in family, out of scope per this round's own instruction), no BBMP-specific content. Combined with rounds 3-4's findings (5+ karnataka.gov.in/bbmp.gov.in domains, all TLS-blocked), Bengaluru's Roads/Streetlights gap should now be treated as a durable environment limitation — every plausible non-karnataka.gov.in path has been tried and leads either back to the blocked infrastructure or to a dead end.]** |
| **Indore — Water/Roads/Streetlights** | **PROMOTED TO VERIFIED (3 records)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/madhya_pradesh/indore.json (MP_IMC_SMARTCITY_WATER_CHANNEL, MP_IMC_SMARTCITY_ROADS_CHANNEL, MP_IMC_SMARTCITY_STREETLIGHT_CHANNEL), sources/inventory.json. imcindore.mp.gov.in and bmconline.gov.in remain confirmed dead per rounds 4-5, not retried. Instead used Smart City Indore's own domain (smartcityindore.org), specifically its `/grievance-registration/` page — genuinely different from the `/citizen-charter/` page and the ABD Water Supply RFP document already checked in earlier rounds. Confirmed real complaint categories (Garbage, Road & Footpath, Public Toilets, Water Supply, Sewerage/Drainage, Street Light, etc.) and a real phone/email contact via direct fetch. The Citizen Charter page does publish generic project-complaint SLAs (T+3 to T+15 working days across Site/Design/Execution/Permission/Fraud/Other categories) but these were deliberately NOT mapped onto the civic-service categories — the document never states they apply to routine water/road/streetlight complaints, and doing so would be an inference, not a confirmed fact. Indore's first-ever city-specific real records (was previously covered only by the MP statewide CM Helpline record).]** |
| **Varanasi — Roads & Streetlights** | **PROMOTED TO VERIFIED (2 records)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttar_pradesh/varanasi.json (UP_VNN_ROADS_GRIEVANCE_CHANNEL, UP_VNN_STREETLIGHT_GRIEVANCE_CHANNEL), sources/inventory.json. VNN already had a general-grievance record covering these problem types in its description, but it was categorized WASTE_SANITATION only (a schema/retrieval limitation, one category per record) — so it never actually surfaced for Roads/Streetlights category queries. Re-fetched VNN's own home page (nnvns.org.in, a different page from the Citizen Charter already cited) directly, re-confirming the same real toll-free (1533)/SMART KASHI APP channel plus 2 additional real numbers not previously recorded (toll-free 18001805567, landline 0542-2720005). Built 2 new, correctly-categorized records using this same real, re-confirmed channel. e-nagarsewaup.gov.in's own grievance page and varanasi.nic.in's grievance-lodging page were also checked but redirect to UP-statewide systems (jansunwai.up.nic.in) with no VNN-specific or category-specific content.]** |

### Net result of Round 6 (final round)

6 new VERIFIED records across 3 of the 4 targeted gaps (Delhi MCD Streetlights, Indore's 3
categories, Varanasi's 2 categories). Bengaluru's Roads/Streetlights gap is now confirmed
exhausted across 3 rounds of genuinely distinct attempts and should be treated as a durable
environment limitation, not a remaining research opportunity, unless this environment's TLS/DNS
capabilities change. This closes out the RAG knowledge-base data-foundation research effort for
PR #13 — no further rounds planned.

## Round 5, continued: Priority-2 partial-coverage sweep (2026-08-14, same session)

Follow-up pass at the 10 Priority-2 partial-coverage gaps flagged for this round. Time allowed a
real attempt at 6 of the 10; the other 4 (Bengaluru, Bhopal/Indore, Varanasi) were not reached this
pass. 1 city (Ahmedabad) produced 3 new records; the rest came up empty after a genuine attempt.

| City / Gap | Result | Notes |
|---|---|---|
| **Ahmedabad, Gujarat (Water/Drainage + Roads/Streetlights)** | **PROMOTED TO VERIFIED (3 records)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/gujarat/ahmedabad.json (GJ_AMC_AMCCRS_WATER_DRAINAGE_CHANNEL, GJ_AMC_AMCCRS_ROADS_CHANNEL, GJ_AMC_AMCCRS_STREETLIGHT_CHANNEL), sources/inventory.json. AMC's own AMCCRS portal (amccrs.com) — a genuinely different domain from the TLS-blocked ahmedabadcity.gov.in confirmed exhausted in round 4 — gave real, specific complaint categories for all 3 gap categories, plus a real 24x7 toll-free (155303), SMS shortcode (56767), email, and WhatsApp channel, all confirmed via direct fetch of the AMCCRS homepage itself (not just a WebSearch summary). No numeric SLA published. Closes Ahmedabad's Water gap entirely and its Roads/Streetlights gap (previously only Surat had these).]** |
| **Patna, Bihar (Water/Roads/Streetlights)** | **NO NEW RECORDS** | **[CHECKED — pmc.bihar.gov.in/drainage.aspx gives only the same general PMC office contact already recorded for Waste (0612-2223791), no drainage-specific content. Guessed URLs for a Water Board Details page and a Street Lights Installation page both returned empty content. The Citizen Charter landing page still does not link to an actual downloadable charter document. Patna's Water/Roads/Streetlights gaps remain open.]** |
| **Gurugram, Haryana (Water/Roads/Streetlights)** | **NO NEW RECORDS** | **[CHECKED — mcg.gov.in/GriMaster.aspx returns only a page title, no body content (same JS-rendering issue confirmed for mcg.gov.in generally in an earlier round). wssbilling.mcg.gov.in (a subdomain found via search) fails DNS resolution entirely (ENOTFOUND). A real LED-streetlight toll-free number (18001803580) was found only via a WebSearch-surfaced tweet from MCG's own account — not independently confirmed via a direct fetch of an official page, so NOT promoted per the primary-source-only rule. Gurugram's gaps remain open.]** |
| **Bengaluru, Karnataka (Roads/Streetlights)** | **NOT ATTEMPTED THIS PASS** | Per the round's own instructions, BBMP/GBA-domain retries were explicitly skipped (confirmed TLS-blocked across 2 prior rounds); no genuinely new non-karnataka.gov.in domain surfaced during this pass's other searches. Remains open. |
| **Bhopal & Indore, Madhya Pradesh (Water/Roads/Streetlights)** | **CONFIRMED DEAD END (Bhopal)** | **[CHECKED — bhopalmunicipal.com (the alternate domain suggested for this round) returns HTTP 403 Forbidden on direct fetch. bmconline.gov.in remains confirmed-empty from round 4. A real general phone/email (+91-755-2701222, commoffice@bmconline.gov.in) surfaced only via WebSearch, never independently confirmed via a direct fetch of an official page, so NOT promoted. Indore was not re-attempted this pass (imcindore.mp.gov.in already confirmed empty twice). Both remain open.]** |
| **Lucknow, Uttar Pradesh (Water/Roads/Streetlights)** | **NO NEW RECORDS** | **[CHECKED — the one specific PDF found via search (lmc.up.nic.in/ViewPDF.ashx?Id=506) was fetched and fully read via the Read-tool-on-saved-PDF workaround: it is a real LMC document, but a 2016 PPP tender notice for a housing scheme, completely unrelated to citizen complaint SLAs. It does confirm a real general LMC phone/fax (0522-2622440) and email (nnlko@up.nic.in), already redundant with what's likely available elsewhere. Lucknow's Water/Roads/Streetlights gaps remain open.]** |
| **Varanasi, Uttar Pradesh (Roads/Streetlights)** | **NOT ATTEMPTED THIS PASS** | Not reached due to time; remains open. |
| **Howrah, West Bengal (Waste/Water/Streetlights)** | **NO NEW RECORDS** | **[CHECKED — myhmc.in/grs/ (grievance system) and myhmc.in/departments-2/ both return HTTP 403 Forbidden on direct fetch (the domain appears to block automated/bot requests generally). A real toll-free number (1800 121 500 000) and support line (033-2638 3211) surfaced only via WebSearch, never independently confirmed via a direct fetch, so NOT promoted. Howrah's Waste/Water/Streetlights gaps remain open (only the state-wide WB PWD Roads record currently covers Howrah).]** |

### Net result of the Priority-2 pass

3 new VERIFIED records (Ahmedabad only). 5 of the remaining 9 gaps (Patna, Gurugram, Bhopal,
Lucknow, Howrah) were genuinely attempted and confirmed still-open with specific reasons logged.
4 (Delhi Streetlights, Bengaluru, Indore, Varanasi) were not reached this pass due to time —
flagged as remaining work for a follow-up round.

## Round 5 (2026-08-14, session 5): first pass at Priority-1 zero-coverage cities

Large push targeting the 10 cities that previously had ONLY synthetic placeholders (never
researched at all), plus a planned Priority-2 sweep of partial-coverage gaps. This entry covers
the Priority-1 portion completed this pass: 8 of 10 cities produced real, promotable VERIFIED
records (13 total); 2 (Jodhpur, and Vijayawada beyond what's noted) did not.

| City / State | Result | Notes |
|---|---|---|
| **Vijayawada, Andhra Pradesh** | **NO NEW RECORDS** | **[CHECKED — `vijayawada.cdma.ap.gov.in` (a real, working ULB-profile subdomain, distinct from the blocked `ourvmc.org`) was fetched directly: its Grievances Dashboard and Contact pages are both client-rendered shells with zero static content ("No category data available"). A follow-up lead via the 2003-era "Citizen's Charters of Select Departments of GoAP" compilation (cgg.gov.in) was fully read (25+ pages) -- it is real and does contain a generic Municipal Administration/Urban Local Bodies section with real day-based grievance times (garbage 1 day, drains 2 days, streetlights 5 days, road cuts 7 days) but is a **pre-2014 undivided-Andhra-Pradesh** state circular, not specific to Vijayawada or current-day AP -- attributing it to "Vijayawada" would misrepresent its actual scope, so NOT promoted. Vijayawada remains open.]** |
| **Gaya, Bihar** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/bihar/gaya.json (BR_GMC_GENERAL_GRIEVANCE_CHANNEL), sources/inventory.json. Real toll-free number (1800 121 8545) confirmed via direct fetch of gayamunicipal.net; general channel only, SLA NOT FOUND. Gaya's first-ever real record.]** |
| **New Delhi (NDMC)** | **PROMOTED TO VERIFIED (3 records)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/delhi/new_delhi.json (DL_NDMC_WATER_SLA, DL_NDMC_ROADS_SLA, DL_NDMC_GENERAL_GRIEVANCE_CHANNEL), sources/inventory.json. ndmc.gov.in/SLA.aspx gave real numeric SLAs (new water connection 35 days, tanker booking 1 day, manhole covers 2 days, road obstruction removal 1 day, road-cutting permission 7 days); ndmc.gov.in/complaints.aspx gave real general-channel contacts (1533, WhatsApp, NDMC 311 app) for waste/streetlights, SLA NOT FOUND for those two. NDMC is confirmed as a genuinely distinct civic authority from Delhi's MCD, already covered separately in delhi/delhi.json. NDMC's first-ever real records.]** |
| **Faridabad, Haryana** | **PROMOTED TO VERIFIED (thin)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/haryana/faridabad.json (HR_MCF_GENERAL_GRIEVANCE_CHANNEL), sources/inventory.json. The Solid Waste Bye-laws 2019 PDF flagged unreadable in round 3 was RE-FETCHED and successfully read in FULL this pass (25 pages) via the Read-tool-on-saved-PDF workaround -- confirmed genuinely real (Municipal Corporation Faridabad, effective 01-01-2021, signed by the Commissioner) but confirmed to contain NO complaint-SLA content at all; it is purely a generator-obligations/segregation-rules/fines-schedule document. Real general contact number (0129-2416464) confirmed instead via faridabad.nic.in. Faridabad's first-ever real record.]** |
| **Mysuru, Karnataka** | **PROMOTED TO VERIFIED (thin)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/karnataka/mysuru.json (KA_MCC_GENERAL_GRIEVANCE_CHANNEL), sources/inventory.json. MCC's own domain (mysurucity.mrc.gov.in) failed with ECONNREFUSED on 2 separate attempts -- a DIFFERENT failure class from the karnataka.gov.in/bbmp.gov.in TLS cert-chain issue confirmed in rounds 3-4, meaning this is not simply "the same Karnataka block" — genuinely a separate connectivity issue on MCC's own domain. Fell back to the district portal (mysore.nic.in) for a real, confirmed general contact (phone + email), SLA NOT FOUND. Mysuru's first-ever real record.]** |
| **Thiruvananthapuram, Kerala** | **PROMOTED TO VERIFIED (2 records)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/kerala/thiruvananthapuram.json (KL_TVM_WATER_SEPTAGE_CHANNEL, KL_TVM_STREETLIGHT_CHANNEL), sources/inventory.json. The Smart Trivandrum civic services portal (smarttvm.tmc.lsgkerala.gov.in) gave real, distinct 24x7 help-desk numbers for Water/Septage and Street Lights. No numeric complaint-SLA published (only an aggregate "92% within SLA" statistic and a 2-hour tanker-delivery window, which is a service window not a complaint SLA). A separate Citizen Charter page (tmc.lsgkerala.gov.in/en/citizen--charter) links to a PDF ("Pauravakasha Rekha.pdf") not yet mined -- flagged as a follow-up lead. Thiruvananthapuram's first-ever real records.]** |
| **Nagpur, Maharashtra** | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/maharashtra/nagpur.json (MH_NMC_WATER_RTS_SLA), sources/inventory.json. NMC's own Right to Services (RTS) Act page (nmcnagpur.gov.in/nmc-rts) gave real numeric SLAs for water services (new connection 15 days, billing/no-dues 3 days, reconnection 15 days). The page does not cover roads/streetlights/waste specifically. NMC's /grievance and /grievance-redressal pages were also checked but had no numeric SLA or department contacts. Nagpur's first-ever real record.]** |
| **Jodhpur, Rajasthan** | **NO NEW RECORDS** | **[CHECKED — jodhpurmc.org's homepage returned empty content on direct fetch; no citizen charter, SLA, or department-contact page could be located via WebSearch either (only third-party aggregator/social-media mentions of a "Jodhpur-311" app). Jodhpur remains fully open -- worth a dedicated retry with different URL guesses in a future round.]** |
| **Chennai, Tamil Nadu** | **PROMOTED TO VERIFIED (3 records)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/tamil_nadu/chennai.json (TN_GCC_WASTE_ROADS_CLEANING_CONTACT, TN_GCC_STREETLIGHT_CONTACT, TN_GCC_ROADS_MAINTENANCE_CONTACT), sources/inventory.json. GCC's own complaints directory (chennaicorporation.gov.in/gcc/complaints/) gave a rich, real, named-officer directory with direct phone numbers for road/street cleaning, streetlight complaints, and road maintenance, including 15 zone-specific Executive Engineer numbers -- comparable richness to Jaipur's charter, though without numeric SLAs. Water supply/drainage was not listed on this specific page and remains open for Chennai. Chennai's first-ever real records (distinct from Coimbatore, already covered).]** |
| **Warangal, Telangana** | **PROMOTED TO VERIFIED (thin)** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/telangana/warangal.json (TS_GWMC_GENERAL_GRIEVANCE_CHANNEL), sources/inventory.json. gwmc.gov.in -- flagged as a real quality-A lead in the ORIGINAL research pass but never independently fetched until now -- confirmed a real call center number and online grievance portal, but neither gwmc.gov.in/grievance_registration.aspx nor ContactUs_New.aspx published department-specific contacts or numeric SLA. Warangal's first-ever real record.]** |

### Net result of Round 5 (Priority-1 portion)

13 new VERIFIED records across 8 cities that previously had zero real coverage (Gaya, New Delhi/NDMC,
Faridabad, Mysuru, Thiruvananthapuram, Nagpur, Chennai, Warangal). 2 cities (Vijayawada, Jodhpur)
remain fully open after a genuine attempt each. The Priority-2 partial-coverage sweep (Patna, Delhi
streetlights, Ahmedabad, Gurugram, Bengaluru, Bhopal/Indore, Lucknow, Varanasi, Howrah) was not
reached this pass due to the scope of the Priority-1 work -- flagged as remaining work for a
follow-up round.

Every government-data URL investigated for this project, logged so effort isn't repeated. Ordered
roughly chronologically. "Result" is the ground truth as of the date checked — government sites do
migrate, so re-verify before assuming a "dead end" entry is still dead.

| Date | URL / target | Result | Notes |
|---|---|---|---|
| 2026-08-09 | `https://data.gov.in/...` (any catalog page) | **BLOCKED** | `data.gov.in` returns HTTP 403 to automated fetching (WebFetch). Not usable as a source-fetch target at all, regardless of which dataset. |
| 2026-08-09 | `https://www.data.gov.in/catalog/solid-waste-management-bareilly` (as given in brief) | **DEAD** | This exact URL does not resolve to real content. |
| 2026-08-09 | `https://smartcities.data.gov.in/catalog/solid-waste-managementbareilly-2` (corrected subdomain, found via WebSearch) | **DEAD (empty catalog entry)** | The dataset has migrated to `smartcities.data.gov.in`, confirming govt data has partially moved subdomains — but this specific catalog page shows "No Result Found... Published on Data Portal: NA". No usable structured content, even though the page itself loads. |
| 2026-08-09 | data.gov.in street-light dataset for Tumakuru (as given in brief) | **NOT FOUND** | WebSearch could not locate this dataset at any indexed URL. Likely never existed at the referenced location, or was removed/never indexed. |
| 2026-08-09 | `https://www.tnurbantree.tn.gov.in/melur/citizen-charter/` | **UNREACHABLE** | First attempt (WebFetch): SSL certificate validation failure (`unable to verify the first certificate`). Second attempt on a different TN municipality (Virudhunagar), same domain: same SSL failure. Third attempt via `curl -k` (skip cert validation) with `-v`: TCP connection succeeds, TLS handshake is sent, then the connection hangs and times out after 15s with 0 bytes received (`Operation timed out after 15003 milliseconds with 0 bytes received`). This is not just a cert problem — the server is not completing the TLS handshake / responding at all from this environment. Confirmed unreachable via 3 independent method attempts (WebFetch x2, curl -k x1). **Conclusion: `tnurbantree.tn.gov.in` is not usable as a source domain from this environment.** A human using a normal browser may still be able to reach it (browsers are far more permissive about legacy/misconfigured TLS than curl/requests) — worth re-attempting manually outside this pipeline if TN coverage becomes a priority later. |
| 2026-08-09/10 | `https://urban.odisha.gov.in/sites/default/files/2021-05/Draft%20Citizen%20Charter_HUD_Final.pdf` (Housing & Urban Development Dept, Govt of Odisha — "Citizen's Charter (Draft)") | **VERIFIED — SUCCESS** | Fetched successfully (805KB PDF, 36 pages). `Read` tool's page-render failed (`pdftoppm is not installed`, no poppler on this Windows machine) — worked around with `pypdf.PdfReader` (pure-Python, already installed) to extract full text directly. Re-checked live with `curl -s -o /dev/null -w "%{http_code}"` on 2026-08-10: **HTTP 200**. Contains a real, detailed services table (26 services with process time/fees/designated officer/appellate/revisional authority) covering, among others, solid waste lifting, water/sewer pipeline repair, tube-well repair, street light replacement, road cutting permission, and road restoration — plus a state-wide, ULB-type-differentiated grievance escalation matrix (4 levels, response times 7/7/15/30 days for Corporations; 7/7/7/15 days for Councils/NACs) and a citation of the Odisha Right to Public Services (ORTPS) Act 2012's statutory appeal windows (30-90 days to Appellate Authority, 30 days for Appellate Authority to dispose; 30-90 days to Revisional Authority, no statutory disposal deadline). Used as the pilot verified source — see `knowledge_records/verified/odisha/statewide.json`. **Caveat carried into `SourceRecord.notes`:** the document is explicitly a *draft* and states "The next review of the citizen charter is scheduled on July 2016" — i.e. it may be dated/superseded; treated as verified because the PDF itself is real, live, and government-published, not because its content is guaranteed current. |

| 2026-08-10 | `https://e-nigam.punjab.gov.in/MCData/Mohali/CITIZENCHARTER.pdf` (Municipal Corporation, S.A.S. Nagar/Mohali, Punjab) | **VERIFIED — SUCCESS** | Found via WebSearch. Confirmed live (HTTP 200), downloaded (632KB, 17 pages), text extracted via pypdf. Genuinely city-specific (not state-wide like Odisha's), with named officers, personal mobile numbers, toll-free complaint line (1800-137-0007, since Dec 2013), a mobile complaint app ("MC CRAMAT"), a Punjab Right to Service Act 2011 statutory-SLA table, and a full 4-level internal grievance escalation ladder (Table 3) with per-day time norms per level. Used for 4 KnowledgeRecords -- see `knowledge_records/verified/punjab/mohali.json`. |
| 2026-08-10 | `https://enigambackuprestore.blob.core.windows.net/securefilestructure/MC/Patiala/MCSubMenu/pdf/linkpdf_2_20_2025_45_862.CITIZEN%20CHARTER%20MCP.pdf` (Municipal Corporation Patiala, Punjab) | **VERIFIED — SUCCESS** | Found via WebSearch, hosted on the Punjab e-Governance backup/CDN blob storage (not the primary domain, but still an official government-published artifact for the same department, same pattern as `smartcities.data.gov.in` being a legitimate alternate subdomain). Confirmed live (HTTP 200), downloaded (843KB, 22 pages), text extracted via pypdf. References a newer Right to Service Act (2018, vs. Mohali's 2011) whose table covers a *different* set of services (no solid-waste/street-light/roads entries) -- confirmed by reading the actual table rather than assumed to match Mohali's. No multi-level escalation ladder present (unlike Mohali) -- only nodal-officer contacts and general Secretary-level oversight are stated; recorded as such rather than invented. Used for 4 KnowledgeRecords -- see `knowledge_records/verified/punjab/patiala.json`. |
| 2026-08-10 | `http://mcchandigarh.gov.in/?q=citizen-charter` (Municipal Corporation Chandigarh) | **PARTIAL / NOT USED** | Page loads and confirms real complaint-channel details (toll-free 14420, 8 AM-8 PM; phone 0172-2787200; online portal `egov.chandigarhsmartcity.in`; Municipal Commissioner named) but the actual citizen-charter document with per-service SLAs was not present in the fetched page content -- only linked deeper in the site navigation, not yet located as a directly fetchable PDF/page. Not used as a KnowledgeRecord source this pass since no verifiable per-service figures were obtained; flagged as a lead worth revisiting (the toll-free number and portal alone are real and could support a lighter-weight record if a fuller charter document is found later). |

## Leads not yet pursued (future work, not fabricated, not yet checked)

- Other states' Housing/Urban Development or Municipal Administration department citizen charters,
  by analogy to Odisha's (search pattern: `"citizen charter" "<department name>" <state> filetype:pdf`
  or the department's own `.gov.in` site — many states publish an equivalent document).
  `smartcities.data.gov.in` (the corrected subdomain) for OTHER cities' solid-waste/streetlight
  datasets, since the Bareilly-specific page was dead but the subdomain itself loads — an
  as-yet-unconfirmed lead, not verified either way.
- CPGRAMS / PGPortal (`pgportal.gov.in`) as a possible source for a *national*-scope, genuinely
  official escalation reference — not yet attempted.
- Individual large-city municipal corporation websites (BMC, Pune Municipal Corporation, GHMC,
  etc.) sometimes publish citizen charters or SLA documents directly — not yet attempted.

## 2026-08-09/10 batch — 9-state + Maharashtra WebSearch-only candidate pass

The following URLs were found via **WebSearch only** — this environment's WebFetch was completely blocked (tested against government sites, Wikipedia, and even anthropic.com; see `research/rag-knowledge-base/02_source_inventory/*.md` in this repo for the full per-record detail, methodology note, and per-state coverage analysis this batch produced). None of these were independently fetched/read — they are real, currently-indexed URLs (confirmed to exist via search-engine indexing) but their content was not directly confirmed the way this log's earlier VERIFIED entries were (HTTP-200-checked, downloaded, text-extracted). **Do not promote any of these to `VERIFIED` without an actual fetch-and-read pass** — that's the whole reason they're logged here rather than in `knowledge_records/verified/`. A quality rating (A/B/C, per this project's rubric: A = official + specific to the service/location, B = official + general, C = official structured dataset) is given as a rough sourcing-priority signal for whoever picks this up next, not a verification claim.


### Andhra Pradesh

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.gvmc.gov.in/static_content/Grievances.jsp | Modes Of Registering Grievances By The Citizens / IVRS | Greater Visakhapatnam Municipal Corporation (GVMC) | HTML | A | Visakhapatnam | All services (grievance mechanism) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://www.gvmc.gov.in/ | Smart Vizag (GVMC's citizen app) | GVMC | HTML | A | Visakhapatnam | All services (app channel) |
| https://www.data.gov.in/catalog/solid-waste-management-visakhapatnam | Solid Waste Management : Visakhapatnam (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset (catalog) | C | Visakhapatnam | Waste & Sanitation |
| https://services.india.gov.in/service/detail/grievances-for-vijayawada-municipal-corporation-commissioner-andhra-pradesh | Grievances for Vijayawada Municipal Corporation Commissioner | services.india.gov.in (National Government Services Portal, Govt. of India) | HTML | B | Vijayawada | All services (grievance mechanism) |
| https://services.india.gov.in/service/detail/check-status-of-complaints-against-vijayawada-municipal-corporation-1 | Check status of complaints against Vijayawada Municipal Corporation | services.india.gov.in | HTML | B | Vijayawada | All services (status check) |
| https://vijayawada.cdma.ap.gov.in/services | Vijayawada Municipal Corporation — Online Services | Vijayawada Municipal Corporation, via CDMA AP State Portal | HTML | A | Vijayawada | All services (ULB portal) |
| http://www.ourvmc.org/jnnurm/ch414.pdf | Vijayawada City Development Plan — Ch.4.14 (JNNURM) | Vijayawada Municipal Corporation (via ourvmc.org, VMC's own legacy domain) | PDF | B | Vijayawada | Water & Drainage |
| https://www.cdma.ap.gov.in/others/portal-info/citizen-charter/ | Citizen Charter | CDMA — Commissioner & Director of Municipal Administration, Govt. of Andhra Pradesh | HTML | B | (state-wide, applies to both cities) | All services |
| https://cdma.ap.gov.in/services/grievances/ | Grievances | CDMA, Govt. of Andhra Pradesh | HTML | B | (state-wide) | All services |
| https://cdma.ap.gov.in/initiatives/puramithra/ | Puramithra Initiative | CDMA, Govt. of Andhra Pradesh | HTML | B | (state-wide) | All services |
| https://pgrs.ap.gov.in/Dashboard/OfficerDashboard | Public Grievance Redressal System (PGRS) | Government of Andhra Pradesh | Web app | B | (state-wide) | All services |

### Gujarat

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://ahmedabadcity.gov.in/portal/jsp/Static_pages/pi_ccharter.jsp | Citizen Charter :: Ahmedabad Municipal Corporation | Ahmedabad Municipal Corporation (AMC) | HTML | A | Ahmedabad | All services (Citizen Charter) | **[CHECKED — 404, page no longer exists. Not usable, not re-tried.]** |
| https://ahmedabadcity.gov.in/StaticPage/solid_waste_mgmt | Solid Waste Management — AMC | AMC | HTML | A | Ahmedabad | Waste & Sanitation |
| https://ahmedabadcity.gov.in/Images/_SWM%20Dept_SWM%20BREIF%20NOTE%20IN%20ENGLISH.pdf | SWM Dept — Brief Note (PDF) | AMC | PDF | A | Ahmedabad | Waste & Sanitation | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://amccrs.apphost.in/AMCPortal | AMCCRS — Comprehensive Complaint Redressal System | AMC | Web app | B | Ahmedabad | All services (complaint portal) |
| https://www.suratmunicipal.gov.in/Downloads/CitizenCharter | Citizen Charter | Surat Municipal Corporation (SMC) | HTML | A | Surat | All services (Citizen Charter) | **[CHECKED — page loads, but it's a generic index linking to dept-specific charters (Watch & Ward, Fire, Town Planning, Shops & Establishments, Law, etc.) with NO SWM/Water/Roads/Streetlight-specific PDF among them. Not usable as-is. The actual targets for our 4 categories are the separate department pages already listed above (rows 63-67: solidwastemanagementhome, drainageintroduction, DrainageHowDoI, hydraulichome, StreetLightsHome) — try those instead.]** |
| https://www.suratmunicipal.gov.in/departments/solidwastemanagementhome | Solid Waste Management Home | SMC | HTML | A | Surat | Waste & Sanitation | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://www.suratmunicipal.gov.in/departments/drainageintroduction | Drainage — Introduction | SMC | HTML | A | Surat | Water & Drainage |
| https://www.suratmunicipal.gov.in/Departments/DrainageHowDoI | Drainage — How Do I Get a Connection? | SMC | HTML | A | Surat | Water & Drainage |
| https://www.suratmunicipal.gov.in/departments/hydraulichome | Water Supply (Hydraulic) — Home | SMC | HTML | A | Surat | Water & Drainage |
| https://www.suratmunicipal.gov.in/Departments/StreetLightsHome | Streetlight — Home | SMC | HTML | A | Surat | Streetlights | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json. Follow-up candidate surfaced by this page's own nav: https://www.suratmunicipal.gov.in/Departments/NonWorkingStreetlights (not yet fetched) likely has the actual complaint procedure.]**
| https://www.data.gov.in/catalog/solid-waste-management-surat | Solid Waste Management : Surat (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset (catalog) | C | Surat | Waste & Sanitation |
| https://vmc.gov.in/PublicService.aspx | Public Service — VMC | Vadodara Municipal Corporation (VMC) | HTML | A | Vadodara | All services (public service portal) |
| https://vmc.gov.in/Department_SWM_Approach.aspx | Department — Solid Waste Management Approach | VMC | HTML | A | Vadodara | Waste & Sanitation |
| https://vmc.gov.in/StreetLight.aspx | Street Light — VMC | VMC | HTML | A | Vadodara | Streetlights |
| https://smartcities.data.gov.in/catalog/solid-waste-generated-collected-processed-data-vadodara | Solid Waste Generated/Collected/Processed Data : Vadodara (dataset) | MoHUA Smart Cities Mission, via Smart Cities Mission Data Portal | Dataset | C | Vadodara | Waste & Sanitation |
| https://gwssb.gujarat.gov.in/helpline | Gujarat Water Supply & Sewerage Board (GWSSB) — Helpline | Gujarat Water Supply & Sewerage Board | HTML | B | (state-wide) | Water & Drainage (context/jurisdiction) |
| https://udd.gujarat.gov.in/ | Urban Development & Urban Housing Department | Government of Gujarat | HTML | B | (state-wide) | All services |
| https://enagar.gujarat.gov.in/enagar/login.jsp | eNagar / DigiGOV | Government of Gujarat, Urban Development & Urban Housing Dept. | Web app | B | (state-wide) | All services |
| https://karnataka.data.gov.in/catalog/solid-waste-management-basic-ahmedabad | Solid Waste Management Basic : Ahmedabad | data.gov.in OGD, mirrored under a karnataka.data.gov.in subdomain (domain oddity — see notes) | Dataset (catalog) | C | Ahmedabad (reference, odd domain) | (structured, quality caveat) |

### Karnataka

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://upload.indiacode.nic.in/showfile?actid=AC_KA_71_402_00007_14_1552388734165&type=rule&filename=bbmp_swm.pdf | Bruhat Bengaluru Mahanagara Palike Solid Waste Management Rules (bye-law) | BBMP, via India Code (Govt. of India legislative repository) | PDF | A | Bengaluru | Waste & Sanitation | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://site.bbmp.gov.in/departmentwebsites/BBMPIT/fms.html | Fix My Street / Sahaaya complaint system | BBMP (Information Technology Dept.) | HTML | B | Bengaluru | Waste & Sanitation |
| https://site.bbmp.gov.in/departmentwebsites/BBMPIT/Pothole%20Fix.html | Pothole Fix / Fix My Street app | BBMP (Information Technology Dept.) | HTML | B | Bengaluru | Roads & Potholes |
| https://bwssb.karnataka.gov.in/english | BWSSB — Bangalore Water Supply and Sewerage Board (official site) | BWSSB (state statutory board, NOT BBMP) | HTML | B | Bengaluru | Water & Drainage |
| https://cms.bwssb.gov.in/ | BWSSB citizen/customer portal | BWSSB | Web app | B | Bengaluru | Water & Drainage |
| https://site.bbmp.gov.in/departmentwebsites/PRO/objectives.html | Public Relation Office — BBMP | BBMP | HTML | B | Bengaluru | Streetlights |
| https://ipgrs.karnataka.gov.in/ | Public Grievance Redressal System (iPGRS) | Government of Karnataka, Centre for e-Governance | Web app | B | Bengaluru | All services (grievance escalation) |
| https://www.data.gov.in/catalog/solid-waste-management-bengaluru | Solid Waste Management : Bengaluru (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in / smartcities.data.gov.in | Dataset (catalog) | C | Bengaluru | Waste & Sanitation |
| https://www.data.gov.in/resource/solid-waste-collection-revenue-data-bengaluru-01-01-2019 | Solid Waste Collection Revenue Data Bengaluru as on 01-01-2019 | data.gov.in OGD Platform India | Dataset | C | Bengaluru | Waste & Sanitation |
| http://www.mysurucity.mrc.gov.in/en/citizen-services | Citizen Services — Mysuru City Corporation | Mysuru City Corporation (MCC) | HTML | B | Mysuru | All services |
| https://services.india.gov.in/service/detail/lodge-your-grievance-with-municipal-corporations-of-karnataka | Lodge your grievance with municipal corporations of Karnataka | National Government Services Portal (Govt. of India) | HTML | B | Mysuru | All services (state grievance channel) |
| http://mangalurucity.mrc.gov.in/en/citizen-charter | Citizen Charter — Mangaluru City Corporation | Mangaluru City Corporation (MCC) | HTML | B | Mangaluru | Waste & Sanitation / all services |
| https://www.data.gov.in/keywords/solid-waste-management (Mangaluru catalog entry referenced in search summary; distinct catalog URL not independently confirmed) | Solid Waste Management : Mangaluru (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in | Dataset (catalog) | C | Mangaluru | Waste & Sanitation |
| https://www.1touchmangaluru.com/ | OneTouch Mangaluru Smartcity | Mangaluru Smart City Ltd. (state/city-govt SPV; non-.gov.in domain) | Web app | B | Mangaluru | All services (Smart City ICCC-style portal) |

### Kerala

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://kochicorporation.lsgkerala.gov.in/en/form/public-grievance-cellnew | Public Grievance Cell | Cochin Corporation (LSGD Kerala) | HTML | B | Kochi | All services (grievance cell) |
| https://mykochi.lsgkerala.gov.in/index/complaint | My Kochi — Complaints | Cochin Corporation (LSGD Kerala) | Web app | B | Kochi | All services (app/portal) |
| https://mykochi.lsgkerala.gov.in/index/complaintstatus | My Kochi — All Complaints / Complaint Status | Cochin Corporation (LSGD Kerala) | Web app | B | Kochi | All services (status check) |
| https://kochicorporation.lsgkerala.gov.in/en/solid-waste-management/368 | Solid Waste Management | Cochin Corporation (LSGD Kerala) | HTML | A | Kochi | Waste & Sanitation |
| https://kochicorporation.lsgkerala.gov.in/en/list-empanelment-agencies-solid-waste-management/491 | List of Empanelment Agencies for Solid Waste Management | Cochin Corporation (LSGD Kerala) | HTML | A | Kochi | Waste & Sanitation |
| https://kochicorporation.lsgkerala.gov.in/system/files/2022-02/Septage_management_bylaw.pdf | Septage Management Byelaw (Draft) | Cochin Corporation (LSGD Kerala) | PDF | A | Kochi | Water & Drainage (sewerage) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://kochicorporation.lsgkerala.gov.in/en/engineering | Engineering | Cochin Corporation (LSGD Kerala) | HTML | B | Kochi | Roads & Potholes / Streetlights |
| https://tmc.lsgkerala.gov.in/en/grievances-redressal-mechanism/1749 | Grievances Redressal Mechanism | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | HTML | B | Thiruvananthapuram | All services (grievance mechanism) |
| https://tmc.lsgkerala.gov.in/en/public-grievance-cell | Public Grievance Cell | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | HTML | B | Thiruvananthapuram | All services (grievance cell) |
| https://tmc.lsgkerala.gov.in/en/organisational-structure | Organisational Structure | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | HTML | B | Thiruvananthapuram | All services (organisational structure) |
| https://tmc.lsgkerala.gov.in/en/solid-waste-management | Solid Waste Management | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | HTML | A | Thiruvananthapuram | Waste & Sanitation |
| https://tmc.lsgkerala.gov.in/en/kharamaalainaya-nairamaarajajanam | Solid Waste Disposal | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | HTML | A | Thiruvananthapuram | Waste & Sanitation |
| https://tmc.lsgkerala.gov.in/en/english | Capital city, Clean city — waste collection centres and calendar | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | HTML | A | Thiruvananthapuram | Waste & Sanitation |
| https://tmc.lsgkerala.gov.in/en/engineering | Engineering | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | HTML | A | Thiruvananthapuram | Roads & Potholes / Streetlights |
| https://smarttvm.tmc.lsgkerala.gov.in/ | Smart Trivandrum — Civic Services Portal | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | Web app | A | Thiruvananthapuram | All services (integrated portal) |
| https://smarttvm.tmc.lsgkerala.gov.in/complaint/report | Smart Trivandrum — Report a Complaint | Thiruvananthapuram Municipal Corporation (LSGD Kerala) | Web app | A | Thiruvananthapuram | All services (complaint intake) |
| https://kwa.kerala.gov.in/en/consumer-grievances/ | Consumer Grievances | Kerala Water Authority (KWA) | HTML | A | Kochi + Thiruvananthapuram | Water & Drainage |
| https://kwa.kerala.gov.in/en/contact-us/ | Contact Us — KWA | Kerala Water Authority (KWA) | HTML | B | Kochi + Thiruvananthapuram | Water & Drainage |
| https://kwa.kerala.gov.in/en/citizen-corner/ | Consumers Corner | Kerala Water Authority (KWA) | HTML | B | Kochi + Thiruvananthapuram | Water & Drainage |
| https://aqualoom.kwa.kerala.gov.in/ | Aqualoom — KWA online complaint system | Kerala Water Authority (KWA) | Web app | A | Kochi + Thiruvananthapuram | Water & Drainage |
| https://lsgkerala.gov.in/en/resources/citizen-charter | Citizen Charter | Local Self Government Department (LSGD), Govt. of Kerala | HTML | B | (state-wide) | All services (citizen charter) |
| https://lsgd.kerala.gov.in/en/waste-management/solid-waste-management/policy-guidelines/ | Policy & Guidelines — Solid Waste Management | Local Self Government Department (LSGD), Govt. of Kerala | HTML | B | (state-wide) | Waste & Sanitation |
| https://lsgkerala.gov.in/index.php/en/public-grievance-redressal-mechanism | Public Grievance Redressal Mechanism | Local Self Government Department (LSGD), Govt. of Kerala | HTML | B | (state-wide) | All services (grievance mechanism, general) |
| (referenced via search summary of Kochi Corporation grievance escalation; no independently confirmed CMPGRC URL surfaced in this pass) | Chief Minister's Public Grievance Redressal Cell (CMPGRC) reference | Government of Kerala | — | B | Kochi | All services (state appellate escalation) |
| https://play.google.com/store/apps/details?id=com.iroads.pwd.pwd4u&hl=en_IN | PWD4U app listing | Public Works Department, Govt. of Kerala (per Play Store listing description) | App store listing | B | Roads (state-wide, jurisdiction-split reference) | Roads & Potholes |
| https://kerala.data.gov.in/ | kerala.data.gov.in OGD portal | Open Government Data (OGD) Platform India — Kerala instance | Dataset portal | C | (national, indexes Kerala datasets) | Waste & Sanitation |

### Maharashtra

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.pmc.gov.in/en/grievance-redressal | GRIEVANCE REDRESSAL MECHANISM | Pune Municipal Corporation | HTML | B | Pune | All services (grievance mechanism) |
| https://complaint.pmc.gov.in/ | तक्रार :मुख्यपृष्ठ (Complaint Portal) | Pune Municipal Corporation | Web app | B | Pune | All services (complaint portal) |
| https://www.pmc.gov.in/en/b/pmc-apps-store | PMC Apps Store — PMC Road Mitra | Pune Municipal Corporation | HTML | B | Pune | Roads & Potholes |
| https://services.india.gov.in/service/detail/check-complaint-status-for-pune-municipal-corporation-maharashtra | Check Complaint Status for PMC | National Government Services Portal (Govt. of India) | HTML | B | Pune | All services |
| https://dm.mcgm.gov.in/central-complaint-registration-system | Central Complaint Registration System | BMC / Disaster Management, MCGM | HTML | B | Mumbai | All services |
| https://portal.mcgm.gov.in/irj/portal/anonymous/qlcomplaintreg?guest_user=english | Lodging Civic Complaints / Complaint Registration | BMC (MCGM) | Web form | B | Mumbai | All services |
| https://www.mcgm.gov.in/irj/go/km/docs/documents/MCGM%20Department%20List/ChiefEngineerSolidWasteManagement/RTI%20Manuals/CESWM_RTI_E02.pdf | Solid Waste Management dept RTI Manual (Sec 4(1)(b)) | BMC — Chief Engineer, Solid Waste Management | PDF | A | Mumbai | Waste & Sanitation | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://portal.mcgm.gov.in/irj/portal/anonymous/qltendersswm_new | Solid Waste Management (portal section) | BMC (MCGM) | HTML | B | Mumbai | Waste & Sanitation |
| https://portal.mcgm.gov.in/irj/portal/anonymous/qlwardc?guest_user=english | WardC — MyBMC | BMC (MCGM) | HTML | B | Mumbai | All services (ward-level) |
| https://www.nmcnagpur.gov.in/grievance/ | Grievance Redressal System — NMC | Nagpur Municipal Corporation | HTML | B | Nagpur | All services |
| https://nmcnagpur.gov.in/grievance/complaint_form.php | New Complaint Registration | Nagpur Municipal Corporation | Web form | B | Nagpur | All services |
| https://grievanceigr.maharashtra.gov.in/home/contactus | Grievance Redressal System (IGR) | Government of Maharashtra | HTML | B | (state-wide) | All services |
| https://www.data.gov.in/catalog/solid-waste-managementpune | Solid Waste Management: Pune (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset (catalog) | C | Pune | Waste & Sanitation |
| https://www.data.gov.in/resource/solid-waste-management-efficiency-thane-2021 | Solid Waste Management Efficiency in Thane : 2021 | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset | C | Thane | Waste & Sanitation |
| https://www.data.gov.in/resource/d19-solidwastedisposal | D19-SolidWasteDisposal | data.gov.in OGD Platform India | Dataset | C | (national, indexes all states) | Waste & Sanitation |

### Odisha

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.bmc.gov.in/services/sanitation-services | Sanitation Services — BMC | Bhubaneswar Municipal Corporation (BMC) | HTML | B | Bhubaneswar | Waste & Sanitation |
| https://www.bmc.gov.in/services/street-lighting | Street Lighting — BMC | Bhubaneswar Municipal Corporation | HTML | B | Bhubaneswar | Streetlights |
| https://www.bmc.gov.in/services/water-supply-services | Water Supply Services — BMC | Bhubaneswar Municipal Corporation (references PHED as actual supplier) | HTML | B | Bhubaneswar | Water & Drainage |
| https://pheoodisha.gov.in/view-portal-services/2 | Public Health Engineering Organization (PHEO), Odisha — Services | PHED/PHEO Odisha — state department, NOT BMC | HTML | B | Bhubaneswar | Water & Drainage |
| https://pheoodisha.gov.in/portal-contact-us/8 | Contact Us — PHEO Odisha | PHED/PHEO Odisha | HTML | B | Bhubaneswar | Water & Drainage |
| https://citizenservices.bhubaneswar.me/grievance/complaint-registration/grievance | State e-Services Portal — Bhubaneswar Me (grievance) | Bhubaneswar Municipal Corporation, Bhubaneswar Smart City Ltd (BSCL), Bhubaneswar Development Authority (BDA), Capital Region Urban Transport (CRUT) — unified helpline | Web app | B | Bhubaneswar | All services (unified grievance) |
| https://sujog.odisha.gov.in/Deshboard/images/Citizen%20Charter_HUD_Final.pdf | CITIZEN'S CHARTER (Draft) — Housing & Urban Development Department | Government of Odisha — Housing & Urban Development (H&UD) Department, via SUJOG | PDF | A | Bhubaneswar / state-wide | All services (Housing & Urban Development Dept. citizen charter) |
| https://sujog.odisha.gov.in/ | SUJOG — Sustainable Urban Services in a Jiffy | Government of Odisha, H&UD Department | Web app | B | (state-wide) | All services (SUJOG e-governance platform) |
| https://sujog.odisha.gov.in/wns | Services / Water & Sewerage — SUJOG | Government of Odisha, H&UD Department | Web app | B | (state-wide) | Water & Drainage (SUJOG module) |
| https://sujog.odisha.gov.in/pgr | Public Grievance Redressal — SUJOG | Government of Odisha, H&UD Department | Web app | B | (state-wide) | All services (public grievance escalation) |
| https://health.odisha.gov.in/sites/default/files/2024-08/23017%2006082024%20Notification%20formal%20platform%20for%20grievance%20%28E%29.pdf | Notification — formal platform for grievance redressal | Government of Odisha — General Administration & Public Grievance Department | PDF | B | (state-wide, general grievance system, not urban-specific) | All services (escalation levels) |
| https://sujogportal.odisha.gov.in/cuttack/service/complaints/ | Public Grievance Redressal — Cuttack Municipal Corporation | Cuttack Municipal Corporation (CMC), via SUJOG | Web app | B | Cuttack | All services (grievance) |
| https://cmccuttack.odisha.gov.in/index.php/2559-2/ | Grievance — Cuttack Municipal Corporation | Cuttack Municipal Corporation | HTML | B | Cuttack | All services (grievance, CMC own site) |
| https://sujogportal.odisha.gov.in/cuttack/service/water-tax/ | Water & Sewerage — Cuttack, SUJOG | Cuttack Municipal Corporation, via SUJOG | Web app | B | Cuttack | Water & Drainage |
| https://sujogportal.odisha.gov.in/rourkela/service/complaints/ | Public Grievance Redressal — Rourkela Municipal Corporation | Rourkela Municipal Corporation (RMC), via SUJOG | Web app | B | Rourkela | All services (grievance) |
| https://rmc.nic.in/eservices.html | e-Services — Rourkela Municipal Corporation | Rourkela Municipal Corporation | HTML | B | Rourkela | All services (RMC own site) |

### Tamil Nadu

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://chennaicorporation.gov.in/gcc/complaints/ | Public Grievance and Redressal System (PGR) | Greater Chennai Corporation (GCC) | HTML | B | Chennai | All services (complaint portal) |
| https://erp.chennaicorporation.gov.in/pgr/citizen/BeforeReg.do | GCC Public Grievance Redressal — citizen registration | Greater Chennai Corporation | Web form | B | Chennai | All services (PGR web app) |
| https://chennaicorporation.gov.in/gcc/department/storm-water/ | Integrated Storm Water Drain — GCC department page | Greater Chennai Corporation | HTML | B | Chennai | Roads & Potholes / Drainage |
| https://cmwssb.tn.gov.in/complaints-grievance | Complaints and Grievance — CMWSSB | Chennai Metropolitan Water Supply and Sewerage Board (CMWSSB), NOT GCC | HTML | B | Chennai | Water & Drainage |
| https://cmwssb.tn.gov.in/citizencharter | Citizen's Charter — CMWSSB | Chennai Metropolitan Water Supply and Sewerage Board | HTML | B | Chennai | Water & Drainage |
| https://cmwssb.tn.gov.in/complaint-redressal | Complaint Redressal — CMWSSB | Chennai Metropolitan Water Supply and Sewerage Board | HTML | B | Chennai | Water & Drainage |
| https://tn.data.gov.in/catalog/solid-waste-management-chennai-6 | Solid Waste Management : Chennai (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via tn.data.gov.in | Dataset (catalog) | C | Chennai | Waste & Sanitation |
| https://www.data.gov.in/catalog/vehicles-and-land-used-solid-waste-management | Vehicles and Land used for Solid Waste Management | data.gov.in OGD Platform India | Dataset | C | (state-wide, applies to all ULBs) | Waste & Sanitation |
| https://www.tnurbantree.tn.gov.in/ | tnurbantree.tn.gov.in — Urban e-Governance / Government Orders | Directorate of Municipal Administration, Tamil Nadu (covers all municipalities/corporations except Chennai) | HTML | B | (state-wide) | All services (Directorate of Municipal Administration) |
| https://ccmc.gov.in/img/upload/CitizensCharterEnglish1.pdf | Coimbatore City Municipal Corporation Citizen's Charter (PDF) | Coimbatore City Municipal Corporation (CCMC) | PDF | A | Coimbatore | All services (citizen charter) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://ccmc.gov.in/index.php/administration/citizen-charter | Citizen Charter — Coimbatore City Municipal Corporation | CCMC | HTML | A | Coimbatore | All services (citizen charter, HTML) |
| https://payment.ccmc.gov.in/frmGrievancesRegistration.asp | Grievance Registration — CCMC | CCMC | Web form | B | Coimbatore | All services (grievance registration) |
| https://www.data.gov.in/catalog/solid-waste-management-coimbatore | Solid Waste Management : Coimbatore (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in | Dataset (catalog) | C | Coimbatore | Waste & Sanitation |
| https://www.tnurbantree.tn.gov.in/madurai/citizen-charter/ | Citizen Charter — Madurai Corporation | Madurai City Municipal Corporation, hosted via tnurbantree.tn.gov.in (Directorate of Municipal Administration) | HTML | B | Madurai | All services (citizen charter) |
| https://madurai.nic.in/service/how-to-lodge-a-grievance/ | How to lodge your Grievance — Madurai District | District Administration, Madurai (Government of Tamil Nadu, NIC) | HTML | B | Madurai | All services (grievance channel, district) |
| https://www.data.gov.in/catalog/solid-waste-management-madurai | Solid Waste Management : Madurai (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in | Dataset (catalog) | C | Madurai | Waste & Sanitation |
| https://tnega.tn.gov.in/projects/e-sevai | Tamil Nadu e-Sevai Portal (TNeGA) | Tamil Nadu e-Governance Agency | HTML | B | (state-wide) | All services (state escalation) |

### Telangana

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.ghmc.gov.in/CitizenCharter/CitizenCharter-19.06.pdf | Citizen's Charter — Hyderabad | Greater Hyderabad Municipal Corporation (GHMC) | PDF | A | Hyderabad | All services (Citizen's Charter) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://igs.ghmc.gov.in/ | Grievances — Integrated Grievance System (IGS) | GHMC | Web app | A | Hyderabad | All services (grievance system) |
| https://ghmconlinegrievance.cgg.gov.in/ | GHMC Online Grievance | GHMC, hosted via Centre for Good Governance (cgg.gov.in) | Web app | B | Hyderabad | All services (grievance system, alt.) |
| https://www.hyderabadwater.gov.in/application/files/7417/3185/0800/updated_citizen_charter.pdf | Citizen's Charter of HMWSSB | Hyderabad Metropolitan Water Supply & Sewerage Board (HMWSSB) — separate statutory board, NOT GHMC | PDF | A | Hyderabad | Water & Drainage | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://www.hyderabadwater.gov.in/en/index.php/contact-us | Contact Us — HMWSSB | HMWSSB | HTML | A | Hyderabad | Water & Drainage |
| https://gwmc.gov.in/grievance_registration.aspx | Grievance Registration — GWMC | Greater Warangal Municipal Corporation (GWMC) | Web form | A | Warangal | All services (grievance mechanism) |
| https://gwmc.gov.in/ContactUs_New.aspx | Contact Us — GWMC | GWMC | HTML | A | Warangal | All services (contact) |
| https://emunicipal.telangana.gov.in/Grievance_Redressal | Grievance Redressal — CDMA / MA&UD | Commissioner and Director of Municipal Administration (CDMA), MA&UD Dept., Govt. of Telangana | HTML | B | (state-wide) | All services |
| https://www.telangana.gov.in/departments/municipal-administration-urban-development/ | Municipal Administration & Urban Development | Telangana State Portal | HTML | B | (state-wide) | All services |
| https://www.data.gov.in/catalog/solid-waste-disposal-warangal | Solid Waste Disposal : Warangal (dataset) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset (catalog) | C | Warangal | Waste & Sanitation |
| https://smartcities.data.gov.in/resources/solid-waste-collection-vehicle-warangal-2019 | Solid Waste Collection Vehicle : Warangal 2019 | MoHUA Smart Cities Mission, via Smart Cities Mission Data Portal | Dataset | C | Warangal | Waste & Sanitation |

### Uttar Pradesh

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://lmc.up.nic.in/ | Official website | Lucknow Municipal Corporation (LMC) | HTML | B | Lucknow | All services, Waste & Sanitation |
| https://services.india.gov.in/service/detail/grievances-for-lucknow-municipal-corporation-uttar-pradesh-1 | Grievances for Lucknow Municipal Corporation | National Government Services Portal (Govt. of India) | HTML | B | Lucknow | All services (grievance mechanism) |
| https://e-nagarsewaup.gov.in/ | e-NagarSewa UP — ULB Integrations / Online Complaint | Directorate of Local Bodies, Govt. of Uttar Pradesh | Web portal | B | (state-wide, applies to Lucknow/Varanasi/Bareilly ULBs) | All services |
| http://e-nagarsewaup.gov.in/ulbapps/Grievance/onlineComplaint.jsp | e-NagarSewa Online Complaint form | Directorate of Local Bodies, Govt. of Uttar Pradesh | Web form | B | (state-wide) | All services |
| http://www.jklmc.in/ (referenced; not independently opened) | Lucknow Jal Sansthan online complaint system | Lucknow Jal Sansthan / Jal Kal Vibhag | Web app | B | Lucknow | Water & Drainage |
| https://jn.upsdc.gov.in/ | Official Website of Jal Nigam, Uttar Pradesh | Uttar Pradesh Jal Nigam (Urban) | HTML | B | Lucknow | Water & Drainage (state utility, general) |
| https://iccc.smartcities.gov.in/icc/city-details/8b0afddce19abe9d79637044539da127 | Integrated Command and Control Centre (ICCC) — Lucknow | Smart Cities Mission, Govt. of India | HTML | B | Lucknow | Streetlight / Smart City |
| https://lucknowsmartcity.com/ | Lucknow Smart City Portal | Lucknow Smart City Ltd. / Lucknow Municipal Corporation | HTML | B | Lucknow | Streetlight / Smart City |
| https://nnvns.org.in/ | Home — Varanasi Nagar Nigam | Varanasi Nagar Nigam (NNVNS) | HTML | B | Varanasi | All services |
| https://nnvns.org.in:449/nnvns/index.php?option=com_content&view=article&id=57&Itemid=396&lang=en | Departments — Varanasi Nagar Nigam | Varanasi Nagar Nigam (NNVNS) | HTML | B | Varanasi | All services (departments) |
| http://www.jalkalvaranasi.org/pgr/comlaint1.php | Jalkal Varanasi Public Grievance and Redressal System (PGR) | Jal Kal Vibhag, Varanasi Nagar Nigam | Web app | B | Varanasi | Water & Drainage |
| https://varanasi.nic.in/service/how-to-lodge-a-grievance/ | How to lodge a Grievance? | District Varanasi, Government of Uttar Pradesh | HTML | B | Varanasi | All services (grievance escalation) | **[CHECKED — 404, page no longer exists. Not usable, not re-tried. Uttar Pradesh remains 0 verified.]** |
| https://nagarnigambareilly.com/citizen-charter.php | Citizen Charter — Nagar Nigam Bareilly | Bareilly Nagar Nigam | HTML | B | Bareilly | All services (citizen charter) |
| https://nagarnigambareilly.com/addcomplaint.php | Grievances Redressal System — Nagar Nigam Bareilly | Bareilly Nagar Nigam | Web form | B | Bareilly | All services (grievance) |
| https://nagarnigambareilly.com/Download/DOOR_TO_DOOR_COLLECTION.pdf | Door to Door Collection (MSW) — official PDF | Bareilly Nagar Nigam | PDF | B | Bareilly | Waste & Sanitation |
| https://nagarnigambareilly.com/Download/CITY_SANITATION_PLAN.pdf | City Sanitation Plan for Bareilly | Bareilly Nagar Nigam | PDF | B | Bareilly | Waste & Sanitation |
| https://services.india.gov.in/service/detail/grievances-for-bareilly-municipal-corporation-uttar-pradesh-1 | Grievances for Bareilly Municipal Corporation | National Government Services Portal (Govt. of India) | HTML | B | Bareilly | All services (grievance) |
| https://www.data.gov.in/resource/waste-collection-vehicle-data-lucknow2019 | Waste Collection Vehicle Data: Lucknow: 2019 | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset | C | Lucknow | Waste & Sanitation |
| https://www.data.gov.in/resource/solid-waste-management-efficiency-prayagraj-2018 | Solid Waste Management-Efficiency: Prayagraj: 2018 | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset | C | Prayagraj | Waste & Sanitation |
| https://www.data.gov.in/resource/solid-waste-disposal-varanasi-2018 | Solid Waste Disposal in Varanasi: 2018 | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset | C | Varanasi | Waste & Sanitation |
| https://www.data.gov.in/catalog/solid-waste-management-kanpur-nagar | Solid Waste Management: Kanpur Nagar (catalog) | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset (catalog) | C | Kanpur Nagar | Waste & Sanitation |

### West Bengal

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.kmcgov.in/KMCPortal/jsp/ComplaintProcedure.jsp | Complaint Procedure — Official Website of KMC | Kolkata Municipal Corporation | HTML | B | Kolkata | All services (complaint procedure) |
| https://www.kmcgov.in/KMCPortal/ComplaintFormAction.do | KMC Common Complaint e-Form | Kolkata Municipal Corporation | Web form | B | Kolkata | All services (complaint form) |
| https://www.kmcgov.in/KMCPortal/jsp/CitizenCharter.jsp | Citizen Charter — Official Website of KMC | Kolkata Municipal Corporation | HTML | B | Kolkata | All services (citizen charter) |
| https://www.kmcgov.in/KMCPortal/jsp/Solid_Waste_Services.html | Solid Waste Management Services | Kolkata Municipal Corporation | HTML | A | Kolkata | Waste & Sanitation | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://www.kmcgov.in/KMCPortal/jsp/SolidWasteFAQs.jsp | Solid Waste FAQs | Kolkata Municipal Corporation | HTML | A | Kolkata | Waste & Sanitation |
| https://www.kmcgov.in/KMCPortal/jsp/Water_Supply.html | Water Supply Department page | Kolkata Municipal Corporation | HTML | A | Kolkata | Water & Drainage (water supply) |
| https://www.kmcgov.in/KMCPortal/downloads/citizens_charter_water_supply.pdf | Citizens' Charter — Water Supply Department | Kolkata Municipal Corporation | PDF | A | Kolkata | Water & Drainage (water supply — citizen charter) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://www.kmcgov.in/KMCPortal/downloads/citizens_charter_water_supply_2016.pdf | Citizen's Charter of Water Supply Department (2016) | Kolkata Municipal Corporation | PDF | A | Kolkata | Water & Drainage (water supply — citizen charter, dated version) |
| https://www.kmcgov.in/KMCPortal/jsp/WaterConnection.jsp | How to get Water Connection in Your New House? | Kolkata Municipal Corporation | HTML | B | Kolkata | Water & Drainage (new connection) |
| https://www.kmcgov.in/KMCPortal/jsp/SewerageAndDrainageServices.jsp | Sewerage and Drainage Services | Kolkata Municipal Corporation | HTML | A | Kolkata | Water & Drainage (sewerage/drainage) |
| https://www.kmcgov.in/KMCPortal/jsp/Manholes.jsp | Manholes — report to Control Room/Borough Office | Kolkata Municipal Corporation | HTML | A | Kolkata | Water & Drainage (manholes) |
| https://www.kmcgov.in/KMCPortal/jsp/Roads.jsp | Roads Dept. — Official Website of KMC | Kolkata Municipal Corporation | HTML | A | Kolkata | Roads & Potholes | **[CHECKED — page loads, but content is only a table of completed civil-works project statistics (km resurfaced, cost, by year) with no complaint procedure, SLA, or contact info for a citizen reporting a pothole. Not usable as a KnowledgeRecord; downgraded to reference-only.]** |
| https://www.kmcgov.in/KMCPortal/jsp/RoadsContact.jsp | Roads Dept. Contact | Kolkata Municipal Corporation | HTML | A | Kolkata | Roads & Potholes (contact) |
| https://www.kmcgov.in/KMCPortal/jsp/KMCRoadDevelopmentDetails.jsp | List of KMC Road Development Scheme | Kolkata Municipal Corporation | HTML | B | Kolkata | Roads & Potholes (development scheme) |
| https://www.kmcgov.in/KMCPortal/jsp/KMCStreetLight.jsp | Street Lighting — Official Website of KMC | Kolkata Municipal Corporation | HTML | A | Kolkata | Streetlights | **[PROMOTED TO VERIFIED — see knowledge_records/verified/, sources/inventory.json]**
| https://www.kmcgov.in/KMCPortal/jsp/Lighting.html | Lighting Services | Kolkata Municipal Corporation | HTML | A | Kolkata | Streetlights |
| https://www.myhmc.in/contacts/ | Contacts — Howrah Municipal Corporation | Howrah Municipal Corporation (HMC) | HTML | B | Howrah | All services |
| https://www.myhmc.in/grs/ | HMC-GRS — Complaint Submission | Howrah Municipal Corporation | Web form | B | Howrah | All services (complaint submission) |
| https://www.myhmc.in/grs/viewgrsticket.php | View Complaint Status — HMC-GRS | Howrah Municipal Corporation | Web app | B | Howrah | All services (status check) |
| https://howrah.gov.in/service/hmc-related-services/ | HMC related services | District Howrah, Government of West Bengal | HTML | B | Howrah | All services (district portal reference) |
| https://cmo.wb.gov.in/default1.aspx | Our Vision — CMO Grievance Cell (Public Grievance Monitoring System, PGMS) | Government of West Bengal, Chief Minister's Office | HTML | B | (state-wide) | All services (escalation) |
| https://pwd.wb.gov.in/general/login?module=grievance | PWD West Bengal grievance login | Public Works Department, West Bengal | Web app | B | (state-wide) | Roads & Potholes (state PWD, jurisdiction-split reference) |
| https://udma.wb.gov.in/ | Department of Urban Development & Municipal Affairs | Government of West Bengal | HTML | B | (state-wide) | Urban Development (department) |
| https://www.data.gov.in/catalog/solid-waste-managementnewtownkolkata | Solid Waste Management_NewTown_Kolkata | Ministry of Housing & Urban Affairs — Smart Cities Mission, via data.gov.in OGD | Dataset (catalog) | C | New Town, Kolkata | Waste & Sanitation |


## Fresh research pass: 6 previously-unresearched states (2026-08-14)

Assam, Bihar, Delhi, Haryana, Madhya Pradesh, and Rajasthan had zero prior research (no
`02_source_inventory/` entry existed for any of them) -- WebFetch worked in this session (unlike
the environment that produced the original 10-state pass, where it was fully blocked), so these
were fetched and read directly rather than logged as leads only.

### Assam

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://gmc.assam.gov.in/resource/citizen-charter | Citizen's Charter | Guwahati Municipal Corporation (GMC) | HTML | B | Guwahati | All services (citizen charter) | **[CHECKED — page loads, but covers only certificate/license/permit processing timelines (birth/death certs, building NOC, trade license, property assessment), not civic complaint SLAs for garbage/water/road/streetlight. Not usable as a KnowledgeRecord.]** |
| https://gmc.assam.gov.in/portlets/dissatisfied-let-us-know | Dissatisfied? Let Us Know! (grievance page) | Guwahati Municipal Corporation (GMC) | HTML | A | Guwahati | Waste & Sanitation, Water & Drainage, Roads & Potholes, Streetlights | **[PROMOTED TO VERIFIED — see knowledge_records/verified/assam/guwahati.json, sources/inventory.json. Real named officers for water/general grievances, Swachhata App for waste; roads/streetlights only listed with no named contact on this page.]** |

### Bihar

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.pmc.bihar.gov.in/citizen-charter.aspx | Citizen Charter (landing page) | Patna Municipal Corporation (PMC) | HTML | B | Patna | Waste & Sanitation | **[PROMOTED TO VERIFIED (channel-only, SLA NOT FOUND) — see knowledge_records/verified/bihar/patna.json, sources/inventory.json. Links to a fuller Citizen Charter document not reached this pass.]** |
| Bihar Right to Public Services Act, 2011 (indiacode.nic.in / prsindia.org) | Bihar RTPS Act, 2011 | Government of Bihar | PDF/legislation | B | (state-wide) | All services (statutory framework, not yet checked for a municipal-complaint-specific SLA table) | **[NOT YET PURSUED — real act, exists, but no municipal civic-complaint SLA table located this pass. Worth a dedicated fetch of the Act's schedule/annexures.]** |

### Delhi

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://mcdonline.nic.in/portal/citizenCharter | Citizen Charter (Health Trade License) | Municipal Corporation of Delhi (MCD) | HTML | B | Delhi | (covers only trade licenses, birth/death certs, e-mutation, veterinary license — NOT garbage/water/drainage/roads/streetlights) | **[CHECKED — confirmed not usable for civic-complaint SLAs, not assumed.]** |
| https://mcdonline.nic.in/portal/downloadFile/pwm_byelaws_2024_240216075150250.pdf | Plastic Waste Management Bye-laws, 2024 | Municipal Corporation of Delhi (MCD) | PDF | B | Delhi | Waste & Sanitation (plastic-specific regulation, not general garbage-collection complaint SLA) | **[CHECKED — real, live, 5.9MB, dated 23 Jan 2024. Regulatory/prohibition content, not a citizen complaint-response SLA document. Not used as a KnowledgeRecord source for that reason.]** |
| https://mcdonline.nic.in/portal/mService | Services gateway | Municipal Corporation of Delhi (MCD) | HTML | A | Delhi | Waste & Sanitation (general channel) | **[PROMOTED TO VERIFIED (channel-only, SLA NOT FOUND) — see knowledge_records/verified/delhi/delhi.json, sources/inventory.json. Confirms Citizen's Call Center 155305 and MCD311 app.]** |
| https://delhijalboard.delhi.gov.in/jalboard/grievance-redressal-mechanism | Grievance Redressal Mechanism | Delhi Jal Board (DJB) | HTML | A | Delhi | Water & Drainage | **[PROMOTED TO VERIFIED — see knowledge_records/verified/delhi/delhi.json, sources/inventory.json. Real 3-level escalation, 21-day PGC auto-trigger, hotline 1916.]** |
| https://pwddelhi.gov.in/citizen-charter | Citizen Charter | Public Works Department (PWD), Delhi | HTML | B | Delhi | Roads & Potholes, Streetlights | **[PROMOTED TO VERIFIED — see knowledge_records/verified/delhi/delhi.json, sources/inventory.json. General (not category-specific) 24hr-attend/1wk-acknowledge/1mo-interim-reply commitment. Covers PWD-maintained roads only — Delhi's road network is split across PWD/MCD/NHAI by classification.]** |
| https://pwdsewa.pwddelhi.gov.in/Home/SubmitComplaint/ | PWD Sewa — Submit Complaint | Public Works Department (PWD), Delhi | Web app | B | Delhi | Roads & Potholes, Streetlights (complaint submission) | **[UNREACHABLE — DNS resolution failure (`getaddrinfo ENOTFOUND`) from this environment. Real subdomain referenced elsewhere on pwddelhi.gov.in; worth re-attempting.]** |

### Haryana

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.mcg.gov.in (homepage, ApplicationsSummary.aspx "Citizen Charter Dashboard") | Municipal Corporation Gurugram (MCG) site | Municipal Corporation Gurugram | HTML (JS-rendered) | — | Gurugram | All services | **[NOT USABLE — site renders via client-side JS; static fetch returns only the page header/title, no body content, across 3 separate attempts (homepage, dashboard page). Real phone numbers (toll-free 18001801817, grievance +911244753555, garbage-specific 18001025952) surfaced via WebSearch's own aggregated answer, but this project's own rule is that a fact must come from a directly-fetched primary source, not a search-engine summary that may itself be drawing from a third-party aggregator (complainthub.org and similar sites appeared in the same search) — so NOT promoted to VERIFIED. A real, worthwhile lead for a human to re-check with a JS-capable browser, or via the mcg.gov.in citizen-charter PDF if one can be located directly.]** |

### Madhya Pradesh

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://cmhelpline.mp.gov.in/About.aspx | About — CM Helpline (181) | Government of Madhya Pradesh | HTML | A | (state-wide) | Waste & Sanitation, Water & Drainage, Roads & Potholes, Streetlights (general channel) | **[PROMOTED TO VERIFIED (state-wide, channel-only, SLA NOT FOUND) — see knowledge_records/verified/madhya_pradesh/statewide.json, sources/inventory.json.]** |
| https://www.smartcityindore.org/citizen-charter/ | Citizen Charter | Smart City Indore (SPV) | HTML | B | Indore | (Smart City *project* complaint timelines — site/design/execution issue SLAs — not general civic-service complaint SLAs) | **[CHECKED — real page, but it's a Smart City Special Purpose Vehicle project-grievance charter, not a municipal civic-service charter. Not usable as a KnowledgeRecord for garbage/water/road/streetlight complaints.]** |
| https://imcindore.mp.gov.in/grievance | Grievance | Indore Municipal Corporation (IMC) | HTML (JS-rendered?) | — | Indore | All services | **[NOT USABLE — static fetch returned no page content. A WebSearch-aggregated answer claimed a 24-hour SLA / 10-working-day review timeline attributed to "IMC's citizen charter," but this could not be independently re-confirmed by directly fetching and quoting a primary page this pass, so it was NOT promoted to VERIFIED, consistent with this project's own sourcing rule. Worth a dedicated re-attempt.]** |

### Rajasthan

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| http://www.jaipurmc.org/PDF/Auction_MM_RTI_Act_Etc_PDF/CitiChar_SP.pdf | Citizen Charter for Different Urban Services and Utilities | Jaipur Municipal Corporation (JMC) | PDF | A | Jaipur | Waste & Sanitation, Water & Drainage, Roads & Potholes, Streetlights | **[PROMOTED TO VERIFIED — see knowledge_records/verified/rajasthan/jaipur.json, sources/inventory.json. The richest source found in this entire project to date: real per-activity min/max SLA tables across all 4 categories, named 3-level escalation chains, and a full page of real per-zone/per-designation phone numbers. 56.7KB, 11-page PDF; the built-in PDF-to-text path failed on this file's encoding (same failure mode as the earlier BBMP bye-law PDF) — worked around by reading the saved PDF directly with a page-rendering tool instead.]** |

## Follow-up leads surfaced by this pass, not yet pursued

- Surat's `NonWorkingStreetlights` page (https://www.suratmunicipal.gov.in/Departments/NonWorkingStreetlights) — fetched this pass. Real page, but contains only a historical monthly non-working-percentage table (Mar 2017 - Feb 2021), no complaint procedure/SLA. The general online complaint portal (`/OnlineServices/complaint/New`) is linked from the same site but wasn't itself fetched/checked this pass.
- Surat's drainage/water pages (`drainageintroduction`, `DrainageHowDoI`, `hydraulichome`) — already logged as quality-A leads in the Gujarat section above, still not fetched; would close Gujarat's Water/Drainage gap if promoted.
- Kolkata's `citizens_charter_water_supply_2016.pdf` (a differently-dated version of the water charter already used) — not compared against the version actually used; check for content drift if both are ever needed.
- MCD's own citizen-charter PDF for general (non-plastic) solid waste bye-laws — not located this pass; the Plastic Waste Management Bye-laws 2024 found instead are real but out of scope for general garbage-collection SLAs.
- Bihar's Right to Public Services Act, 2011 — real statutory framework, not yet checked for a municipal-complaint-specific SLA schedule.
- Haryana (MCG) and Madhya Pradesh (IMC Indore) — both had promising leads that returned no usable static content (JS-rendered sites); worth a re-attempt with a JS-capable fetch or by locating a direct PDF citizen charter instead of the HTML app shell.

## States still with zero verified coverage after this pass

Uttar Pradesh and Haryana. Uttar Pradesh was researched in the original 10-state pass and came
up empty (a genuine dead end, not "not yet started"). Haryana was attempted fresh this pass
(see above) — real leads exist (MCG) but nothing independently fetchable/quotable was found; a
JS-capable browser or a direct PDF citizen-charter search is the likely next step.


## Continued research pass (2026-08-14, session 2): Haryana retry, UP fresh attempt, category gaps

Follow-up to the "6 previously-unresearched states" pass above. This session: retried Haryana
with a different authority (GMDA instead of MCG), attempted UP fresh with real WebFetch (the
original 10-state pass never touched it beyond WebSearch), and filled category gaps in West
Bengal/Gujarat/Kerala/Karnataka/Maharashtra using already-logged candidate URLs plus new leads.

### Haryana (retry)

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://services.gmda.gov.in/ | Services Portal | Gurugram Metropolitan Development Authority (GMDA) | HTML | B | Gurugram | Waste & Sanitation (general channel) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/haryana/gurugram.json, sources/inventory.json. GMDA is distinct from MCG; real toll-free 18001801817 independently confirmed via direct fetch this time (was previously only seen via an unconfirmed WebSearch summary).]** |
| https://ulbharyana.gov.in/Website/Faridabad/Images/c7f73535-d387-4b0d-8cac-a37a78605b0d.pdf | Solid Waste (Management & Handling) Bye-laws, 2019 | Faridabad Municipal Corporation, via Haryana ULB Directorate | PDF | A (real, unusable) | Faridabad | Waste & Sanitation | **[CHECKED — real, live, 6.5MB PDF. Scanned/image-based with no extractable text layer; this environment has no OCR/poppler available. Genuinely real bye-law, just unreadable by current tooling — worth re-attempting with OCR capability.]** |
| https://www.mcg.gov.in (retry, ApplicationsSummary.aspx) | Municipal Corporation Gurugram | MCG | HTML (JS-rendered) | — | Gurugram | All services | **[STILL NOT USABLE on retry — confirmed via direct fetch that mcg.gov.in genuinely is Municipal Corporation of *Gurugram* (not Ghaziabad, despite one ambiguous WebSearch result), but every page attempted returns only the header/title, no body content.]** |
| https://ulbharyana.gov.in/img/pdf/SWM%20Policy%20and%20Strategy%20on%20Solid%20Waste%20Management.pdf | SWM Policy and Strategy | Directorate of Urban Local Bodies, Haryana | PDF | — (dead) | (state-wide) | Waste & Sanitation | **[CHECKED — 404, page removed/moved. Appeared as a live search result but the URL is dead.]** |
| Gurugram Municipal Corporation Solid Waste Management Bylaws, 2025 (draft) | — | MCG | — | — | Gurugram | Waste & Sanitation | **[NOT YET ENACTED — reported via news sources as still awaiting state government approval; not a usable source until finalized and published.]** |

### Uttar Pradesh (fresh attempt with real WebFetch)

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://lmc.up.nic.in | Official Website | Lucknow Municipal Corporation (LMC) | HTML | B | Lucknow | Waste & Sanitation | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttar_pradesh/lucknow.json, sources/inventory.json. Real Mayor's Office helpline for garbage-overcharging complaints.]** |
| https://jn.upsdc.gov.in | UP Jal Nigam homepage | UP Jal Nigam | HTML | B | (state-wide) | Water & Drainage | **[CHECKED — real contact info (phone, named Web Information Manager) but no citizen charter/SLA on this page. Links to e-nagarsewaup.gov.in.]** |
| https://e-nagarsewaup.gov.in/ | e-Nagar Sewa UP (state ULB citizen services portal) | Government of Uttar Pradesh | HTML | B | (state-wide) | All services | **[NOT USABLE — page only returns a redirect message, no further content reachable this pass.]** |
| https://nnvns.org.in | Varanasi Nagar Nigam homepage | Varanasi Nagar Nigam (VNN) | HTML | A | Varanasi | Waste & Sanitation, Roads & Potholes, Streetlights (general channel + toll-free) | **[CHECKED, led to Citizen Charter page below.]** |
| https://nnvns.org.in/nnvns/index.php?option=com_content&view=article&id=224&lang=en&Itemid=238 | Citizen Charter | Varanasi Nagar Nigam (VNN) | HTML | A | Varanasi | Water & Drainage (real 15-day connection SLA), Waste & Sanitation, Roads & Potholes, Streetlights (general channel) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/uttar_pradesh/varanasi.json, sources/inventory.json.]** |
| https://jalkalvaranasi.org (and /pgr) | Varanasi Jal Kal (water/sewerage board) | Jal Kal Vibhag, Varanasi | HTML | — | Varanasi | Water & Drainage | **[UNREACHABLE — connection refused, 2 separate attempts (base domain and /pgr path).]** |

### West Bengal (Roads gap)

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://pwd.wb.gov.in/general/login?module=grievance | Grievance Portal | Public Works Department, West Bengal | Web app | A | (state-wide, covers Kolkata) | Roads & Potholes | **[PROMOTED TO VERIFIED — see knowledge_records/verified/west_bengal/kolkata.json, sources/inventory.json. Closes the Roads gap left by KMC's own unusable Roads page (see earlier session's log). Real WhatsApp helpline 9073362000, explicitly covers roads/bridges/culverts damage and workmanship complaints.]** |

### Gujarat (Water/Drainage + Roads gaps)

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://www.suratmunicipal.gov.in/departments/hydraulichome | Water Supply (Hydraulic) — Home | Surat Municipal Corporation (SMC) | HTML | A (checked, thin) | Surat | Water & Drainage | **[CHECKED — real page, general complaint channels only (portal, apps), no SLA.]** |
| https://www.suratmunicipal.gov.in/departments/drainageintroduction | Drainage — Introduction | SMC | HTML | A (checked, thin) | Surat | Water & Drainage | **[CHECKED — functions description only, no SLA or contact details.]** |
| https://www.suratmunicipal.gov.in/Departments/DrainageHowDoI | Drainage — How Do I Get a Connection? | SMC | HTML | A | Surat | Water & Drainage | **[PROMOTED TO VERIFIED — see knowledge_records/verified/gujarat/surat.json, sources/inventory.json. Real 15-day connection-approval SLA.]** |
| https://www.suratmunicipal.gov.in/Home/TollFreeNumbers | Toll Free Numbers | SMC | HTML | A | Surat | Roads & Potholes (general channel), Waste & Sanitation (C&D/plastic-specific lines) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/gujarat/surat.json, sources/inventory.json. Used as Surat's general Roads complaint channel (no roads-specific line published).]** |
| https://www.suratmunicipal.gov.in/Departments/RoadDevelopmentHome | Road Development Introduction/Projects | SMC | HTML | A (checked, thin) | Surat | Roads & Potholes | **[CHECKED — completed-project statistics table only, no complaint SLA.]** |
| https://ahmedabadcity.gov.in/portal/jsp/Static_pages/pi_RoadResurfacing.jsp | Road Resurfacing | Ahmedabad Municipal Corporation (AMC) | HTML | A (unreachable) | Ahmedabad | Roads & Potholes | **[UNREACHABLE — SSL certificate verification failure ("unable to verify the first certificate"), same failure class as tnurbantree.tn.gov.in from an earlier pass. Ahmedabad's own Water/Roads gaps remain open; AMC's Comprehensive Complaint Redressal System (155303, already logged elsewhere in this project's Ahmedabad waste record) plausibly covers roads too per a WebSearch summary, but this was not independently confirmed via a directly-fetched primary page, so not promoted.]** |

### Kerala (Waste/Roads/Streetlights gaps)

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://kochicorporation.lsgkerala.gov.in/en/solid-waste-management/368 | Solid Waste Management | Cochin Corporation | HTML | A (checked, thin) | Kochi | Waste & Sanitation | **[CHECKED — only navigation/header content returned, no substantive complaint procedure.]** |
| https://kochicorporation.lsgkerala.gov.in/en/form/public-grievance-cellnew | Public Grievance Cell | Cochin Corporation | Web form | A | Kochi | Waste & Sanitation, Roads & Potholes, Streetlights (general channel) | **[PROMOTED TO VERIFIED — see knowledge_records/verified/kerala/kochi.json, sources/inventory.json. 3 records (Waste/Roads/Streetlights), general channel, no category-specific SLA.]** |

### Karnataka (Water gap; Roads/Streetlights still open)

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://bwssb.karnataka.gov.in/page/Contact+Us/Contact+Info/en | Contact Info | Bangalore Water Supply and Sewerage Board (BWSSB) | HTML | A | Bengaluru | Water & Drainage | **[PROMOTED TO VERIFIED — see knowledge_records/verified/karnataka/bengaluru.json, sources/inventory.json. Real call center 1916, Safai Mitra 14420.]** |
| https://www.bwssb.karnataka.gov.in/all_complaint_details?info=1480 | Complaint details/procedure | BWSSB | HTML | — | Bengaluru | Water & Drainage | **[UNREACHABLE — TLSV1_ALERT_UNRECOGNIZED_NAME (SNI/TLS config issue).]** |
| https://bbmp.sahaaya.in | Sahaaya citizen grievance portal | BBMP | HTML | — | Bengaluru | Roads & Potholes, Streetlights | **[UNREACHABLE — certificate altname mismatch (cert covers *.nammabengaluru.org.in, not this hostname).]** |
| https://sahaaya.nammabengaluru.org.in | Sahaaya (alternate domain) | BBMP | HTML | — | Bengaluru | Roads & Potholes, Streetlights | **[UNREACHABLE — DNS resolution failure.]** |
| https://site.bbmp.gov.in | BBMP official site | BBMP | HTML | — | Bengaluru | Roads & Potholes, Streetlights | **[UNREACHABLE — connection refused.]** |
| BBMP control room 080-22660000, helpline 1533, BESCOM 1912 (streetlight electrical faults) | — | BBMP / BESCOM | — | D (secondary only) | Bengaluru | Roads & Potholes, Streetlights | **[NOT PROMOTED — these numbers surfaced only via WebSearch-aggregated answers citing third-party sites (godigit.com, thinkbangalore.com), never independently confirmed via a directly-fetched primary .gov.in page. Per this project's primary-source-only rule, logged here as an unconfirmed lead, not used as a KnowledgeRecord source. Karnataka's Roads/Streetlights gaps remain genuinely open.]** |

### Maharashtra (Water/Roads/Streetlights gaps + a stronger Waste source)

| URL | Source title | Authority | Format | Quality | Cities | Services |
|---|---|---|---|---|---|---|
| https://portal.mcgm.gov.in/... (Lodging Civic Complaints, Complaint Registration, ContactUs, Water Supply Project pages) | Various | Brihanmumbai Municipal Corporation (BMC/MCGM) | Legacy SAP NetWeaver portal | — | Mumbai | All services | **[SYSTEMATICALLY UNUSABLE — every page on portal.mcgm.gov.in returns only "Could not open iView. The iView is not compatible with your browser..." across 3 separate URLs attempted. This is BMC's primary complaint/contact portal and it is not fetchable by any static tool used this pass.]** |
| https://praja.org/praja_docs/praja_downloads/CITIZEN%20CHARTER.pdf | Citizens' Charter (June 1999) | Municipal Corporation of Greater Mumbai, with PRAJA Foundation | PDF (scanned) | A (real, dated) | Mumbai | Water & Drainage, Roads & Potholes, Streetlights, Waste & Sanitation (supplementary) | **[PROMOTED TO VERIFIED, explicitly flagged OUTDATED / VERIFY BEFORE PRODUCTION — see knowledge_records/verified/maharashtra/mumbai.json, sources/inventory.json. Genuinely MCGM's own signed 1999 charter (Mayor + Municipal Commissioner), hosted by the NGO co-publisher rather than mcgm.gov.in directly — same "real document, third-party-hosted" precedent as the Punjab Patiala charter (Azure blob storage) and BBMP bye-law (India Code) in earlier sessions. 26 years old: every record built from it carries an explicit staleness warning and the phone numbers/contact directory are NOT reproduced in the KnowledgeRecords themselves for that reason (only in this source's own inventory notes, pointing back to the original PDF).]** |

## Follow-up leads surfaced by this session, not yet pursued

- Faridabad's real, live Solid Waste Bye-laws 2019 PDF — needs OCR capability this environment doesn't have.
- Ahmedabad's Roads page and BWSSB's complaint-details page — both blocked by SSL/TLS issues from this environment; a human browser would likely succeed where automated fetch failed.
- BBMP's Sahaaya portal (Roads/Streetlights for Bengaluru) — 3 different hostnames all failed for different technical reasons (cert mismatch, DNS, connection refused); worth a dedicated retry.
- Ahmedabad's own Water/Drainage and Roads categories remain fully open (Surat's were closed this pass; Ahmedabad's were not attempted beyond the failed Roads fetch).
- Varanasi Jal Kal's own dedicated site (jalkalvaranasi.org) — connection refused twice; VNN's general charter was used instead and already covers water with a real SLA, but Jal Kal's own site might have richer detail if it becomes reachable later.

## Round 3 (2026-08-14, session 3): revisiting strongest remaining leads, not a full sweep

Targeted follow-ups on 7 specific leads flagged as strongest-remaining in the previous session, rather
than re-doing full state sweeps. 1 of 7 produced a real, promotable VERIFIED record; the other 6 are
confirmed dead ends (with the specific reason logged for each, per the project's honesty rule).

| URL / target | State | Result | Notes |
|---|---|---|---|
| `https://ulbharyana.gov.in/img/pdf/SWM%20Policy%20and%20Strategy%20on%20Solid%20Waste%20Management.pdf` | Haryana | **DEAD (404)** | **[CHECKED — the URL now returns a 404 error page, not the PDF. Whatever was live at this path in an earlier pass is no longer reachable. Haryana's Waste category remains covered only by GMDA's general-channel record (HR_GMDA_GENERAL_GRIEVANCE), unchanged.]** |
| `https://bbmp.gov.in`, `https://gba.karnataka.gov.in`, `https://support.bbmpgov.in/ehelpline`, `https://ahmedabadcity.gov.in/...` (multiple pages) | Karnataka, Gujarat | **UNREACHABLE (systemic)** | **[CHECKED — every single karnataka.gov.in / bbmp*.in and ahmedabadcity.gov.in URL attempted this round (5 total, both http:// and https://) failed with the identical "unable to verify the first certificate" TLS error, including the AMC PDF `.../PropertyTax_Citizens%20Charter_...pdf` found via search. This is consistent with the SSL cert-chain failures logged against these exact domain families in the previous round (AMC's RoadResurfacing page, BWSSB, BBMP's 3 hostnames) -- confirms this is a systemic TLS-trust-store issue in this environment for Gujarat/Karnataka .gov.in infrastructure specifically, not a URL-specific dead link. BBMP itself was confirmed (via WebSearch, not independently fetched) to have been dissolved 2025-09-02 and replaced by the Greater Bengaluru Authority (GBA) -- so even a working fetch would now need re-scoping to GBA-branded sources. Karnataka Roads/Streetlights and Ahmedabad Water/Drainage+Roads remain genuinely open.]** |
| `https://cdma.ap.gov.in/resources/service-sla`, `https://www.gvmc.gov.in`, `https://cdma.ap.gov.in/sites/default/files/Vijayawada.pdf` | Andhra Pradesh | **DEAD / EMPTY** | **[CHECKED — Vijayawada.pdf (cited by a WebSearch summary as a real SLA-bearing charter) returns HTTP 404. CDMA's own statewide "Service Level Agreements (SLAs)" page at /resources/service-sla is a real, live link but is a client-side-rendered search tool returning "No SLA items found matching your search criteria — Showing 0 of 0 SLA items" with no query applied and no way to browse all items via static fetch. GVMC's own site confirms a "Citizens Charter Rules & Procedures" menu entry exists but no direct URL/PDF for it could be resolved via WebSearch. AP's 4 categories remain covered only by GVMC's generic IVRS/PGRS channel, unchanged.]** |
| `imcindore.mp.gov.in` (both `www.` and bare) | Madhya Pradesh | **EMPTY** | **[CHECKED — page returns genuinely empty content both with and without the www subdomain, same result as the previous round. No citizen charter PDF for Indore Municipal Corporation was located via WebSearch either. The previously-flagged unconfirmed WebSearch claim of a "24hr/10-working-day SLA" for IMC could NOT be confirmed via any direct fetch this round either -- it is NOT promoted and should be treated as unconfirmed, not used. MP remains 1 record (state CM Helpline, channel-only).]** |
| `https://prsindia.org/files/bills_acts/acts_states/bihar/2011/2011Bihar4.pdf`, `https://rtps.bihar.gov.in/rtps/`, `https://pmc.bihar.gov.in/act-rules-policy.aspx` | Bihar | **CONFIRMED FRAMEWORK-ONLY** | **[CHECKED — the Bihar RTPS Act 2011 PDF itself was fetched and read directly; it establishes only the general procedural framework (Designated Public Servants, Appellate/Reviewing Authorities, stipulated time limits to be set by later notification) with no schedule/annexure of actual municipal-service SLAs in this document. The RTPS portal (rtps.bihar.gov.in) lists certificate-type services (caste/income/residence) with no Nagar Nigam/civic-service section. Patna Municipal Corporation's own Acts/Rules/Policy page lists 15 downloadable PDFs (building bylaws, property tax, fire tax, road-cutting regulations) -- none is a citizen-facing complaint-SLA document. Bihar remains 1 record (Patna, channel-only, no SLA).]** |
| `https://mcdonline.nic.in/portal/downloadFile/pwm_byelaws_2024_240216075150250.pdf` | Delhi | **OUT OF SCOPE (confirmed same as before)** | **[CHECKED — this is MCD's Plastic Waste Management bye-laws specifically (PWM = Plastic Waste Management), the same document already ruled out in the previous round as plastic-specific, not general solid-waste. No separate general MCD Solid Waste Management bye-law PDF was located via WebSearch or by fetching mcdonline.nic.in's homepage (which returned empty/JS-driven content).]** |
| `https://mcdonline.nic.in/portal/downloadFile/slb_final-converted_230110051610110.pdf` (MCD Swachh Bharat Mission — Service Level Benchmarking Handbook) | Delhi | **PROMOTED TO VERIFIED** | **[PROMOTED TO VERIFIED — see knowledge_records/verified/delhi/delhi.json (DL_MCD_SLB_TOILET_SANITATION_SLA), sources/inventory.json. WebFetch's own text extraction returned only garbled binary/font data (scanned-style PDF, same failure class as the Jaipur/Mumbai charters) -- worked around by pointing the Read tool at the locally-saved copy, which rendered the page content directly. Item 20 of the SLB monitoring guideline states: "Complaint registration and redressal mechanism is in place and functioning, all complaints, maintenance issues or incidents must be resolved within 24 hours" -- a real, numeric, primary-sourced SLA, but scoped specifically to community/public toilets (CTs/PTs), NOT general household garbage collection. The rest of the document (a %-based Service Level Benchmarking scorecard for water/sewerage/SWM) reports performance percentages, not time-bound complaint SLAs, so was not used for anything beyond the toilet-specific 24-hour figure. DL_MCD_WASTE_GRIEVANCE_CHANNEL (general garbage, SLA NOT FOUND) is left unchanged and NOT merged with this new record -- the two cover genuinely different services.]** |

### States/categories still genuinely open after Round 3

- Haryana: Waste category — only GMDA's general channel (no SLA).
- Karnataka: Roads & Streetlights for Bengaluru — fully open, blocked by systemic TLS issues.
- Gujarat: Ahmedabad's Water/Drainage and Roads — fully open, blocked by systemic TLS issues (Surat is done).
- Andhra Pradesh: all 4 categories still covered only via GVMC's generic IVRS/PGRS channel.
- Madhya Pradesh: still 1 record (state CM Helpline, channel-only).
- Bihar: still 1 record (Patna, channel-only, no SLA) — confirmed no stronger primary source exists via the RTPS Act angle.
- Delhi: general household garbage collection still SLA NOT FOUND (the new toilet-specific 24-hour SLA does not extend to it).

## Uttar Pradesh and Haryana coverage status update

Both states now have real VERIFIED coverage (UP: Lucknow + Varanasi, 3 records; Haryana: Gurugram/GMDA, 1 record) — the "0 verified" gap from the previous session's log is closed for both, though Haryana's coverage remains thin (1 general-channel record, no category-specific SLA) and UP's Waste/Roads/Streetlights (outside Varanasi's water SLA) remain channel-only too.

## Round 4 (2026-08-14, session 4): reaching blocked Karnataka/Gujarat content via alternate paths — zero survivors

Round 3 established that Karnataka's and Gujarat/Ahmedabad's dead ends all shared one root cause: a broken/unusual TLS
cert chain specific to the `karnataka.gov.in` and `ahmedabadcity.gov.in` domain families in this environment (other
`.gov.in` domains fetched fine in the same sessions). This round tested 5 specific ways to reach the same underlying
content through a different, working path, plus 2 alternate-city leads for AP and MP. Result: **0 of 5 leads
survived** — each failed for a distinct, confirmed reason, not from insufficient effort. This is a genuine, useful
negative result: it confirms these are real environment/infrastructure limitations, not a search-effort gap.

| Lead | Target | Result | Notes |
|---|---|---|---|
| Wayback Machine mirrors of blocked BBMP/AMC pages | Karnataka, Gujarat | **TOOL-BLOCKED (blanket)** | **[CHECKED — WebFetch refuses `web.archive.org` entirely: "Claude Code is unable to fetch from web.archive.org", confirmed with 3 different URL forms (a specific snapshot of `bbmp.gov.in`, a specific snapshot of AMC's Road Resurfacing page, and a raw CDX-style listing URL). This is a tool-level restriction, not a per-page or per-snapshot issue -- Wayback Machine is not a usable workaround path in this environment at all, for any domain, not just Karnataka/Gujarat.]** |
| `https://upload.indiacode.nic.in/showfile?...&filename=bbmp_rules_2021.pdf...` (BBMP Advertisement Rules 2021, via India Code's working cert chain) | Karnataka | **REAL BUT OFF-TOPIC** | **[CHECKED — fetched successfully (India Code's domain has no TLS issue, confirming the round-3 hypothesis that it's specific to karnataka.gov.in/bbmp.gov.in, not all Karnataka-related content). Read in full via the Read-tool-on-saved-PDF workaround: this is BBMP's Advertisement Rules 2021, entirely about outdoor hoarding/billboard licensing -- zero content about road repair or streetlight complaint SLAs. Confirms BBMP does publish real bye-laws through India Code (same channel as its existing VERIFIED waste bye-law), but this specific one isn't the right subject matter.]** |
| `https://www.indiacode.nic.in/bitstream/123456789/21664/1/36_of_2025_(e).pdf` (Greater Bengaluru Governance Act, 2024) | Karnataka | **BLOCKED (403)** | **[CHECKED — HTTP 403 Forbidden from indiacode.nic.in itself for this specific bitstream, unlike the showfile-pattern URL above which worked. Not retried a second time per the one-attempt rule; BBMP/GBA's Roads and Streetlights categories remain genuinely open for Bengaluru.]** |
| Gujarat (Right of Citizens to Public Services) Act, 2013 — SMC's own RCPS page, SUDA's page, and the actual 2016 gazette notification PDF (fetched via India Code's working cert chain and read in full via the Read-tool workaround) | Gujarat | **CONFIRMED NO SCHEDULE** | **[CHECKED — 3 fetches. SMC's and SUDA's own pages both link out to a PDF without displaying schedule content inline. The actual Gujarat Government Gazette notification (13-04-2016) under the Act was read in full: it only constitutes State Appellate Authorities per department (19 departments including "Urban Development and Urban Housing", each just assigned an Additional Chief Secretary/Principal Secretary/Secretary as appellate authority) -- no service-specific schedule of civic services with day limits anywhere in this document. Ahmedabad's Water/Drainage and Roads gaps remain open; this state-law angle is now confirmed exhausted, not just unexplored.]** |
| `ourvmc.org` (Vijayawada Municipal Corporation) — home page and a specific RTI Information Handbook PDF (`general/ria2018.pdf`), both via explicit `http://` | Andhra Pradesh | **UNREACHABLE (ECONNREFUSED)** | **[CHECKED — both attempts failed with `connect ECONNREFUSED` on port 443. WebFetch auto-upgrades any http:// URL to https://, and this domain appears to have no working TLS listener at all (a different failure class from Karnataka/Gujarat's broken cert chain, closer to `site.bbmp.gov.in`'s connection-refused failure from round 3). A WebSearch summary claims ourvmc.org hosts both a Citizen Charter and a genuine day-based SLA table, but this could not be independently confirmed by any direct fetch and is NOT promoted. AP's 4 categories remain covered only by GVMC's generic IVRS/PGRS channel.]** |
| `bmconline.gov.in` (Bhopal Municipal Corporation) | Madhya Pradesh | **EMPTY / NO PRIMARY SOURCE** | **[CHECKED — the domain resolves and loads but returns only a bare "App Title" placeholder (client-side-rendered shell), same failure class as `imcindore.mp.gov.in` and `mcdonline.nic.in`'s homepage. A WebSearch for Bhopal-specific grievance/charter content surfaced only a third-party aggregator (complainthub.org) -- explicitly disqualified as a source per this project's primary-source-only rule, not used. MP remains 1 record (state CM Helpline, channel-only).]** |

### Net result of Round 4

No new VERIFIED records this round -- all 5 primary leads (plus 2 sub-leads for BBMP specifically) were run down to a
confirmed, specific dead end rather than left ambiguous. Karnataka (Roads/Streetlights), Gujarat/Ahmedabad
(Water/Drainage + Roads), Andhra Pradesh (all 4 categories, generic-channel-only), Madhya Pradesh (1 record,
channel-only), and Bihar (1 record, channel-only, confirmed exhausted in Round 3) remain open. Given that Round 3
already confirmed Bihar's RTPS Act angle exhausted and this round confirmed Gujarat's RTS Act angle, Karnataka's
Wayback/India-Code angles, AP's ourvmc.org angle, and MP's Bhopal angle are ALL exhausted too, these 5 gaps should
now be treated as durable limitations of this research pipeline (primary-source-only + this environment's TLS/tooling
constraints) rather than leads still worth re-attempting without a genuinely new angle or a change in environment
capabilities (e.g. Wayback Machine access, or a working TLS path to karnataka.gov.in/ahmedabadcity.gov.in/ourvmc.org).
