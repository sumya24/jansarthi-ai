# Location Migration Report

Generated: 2026-08-28T13:11:14.913255+00:00

Maps EXISTING free-text `ward` values (on `users` and `complaints`) onto the structured location hierarchy, wherever an exact, unambiguous match exists against the seeded `wards` table (via LocationResolver.resolve_ward_by_text -- the same logic applied live to new complaints, see routes/complaints.py). No value is ever guessed -- every unmapped row below has an explicit reason, not a silent skip.

## Summary
- Worker rows with a ward set: 108 -- matched: 36, unmapped: 72
- Complaint rows with a ward set: 58 -- matched: 58, unmapped: 0
- Citizens: 0 considered -- `users.ward` is confirmed unused for the citizen role (see docs/location_data_audit.md §2), so there is nothing to migrate for citizens in this pass.
- `home_*_id` (home/registered location -- a different concept from the operational `ward_id` this script populates) is untouched by this script and remains entirely null for every user, worker and citizen alike, until a future opt-in profile-location feature is built.
- No row was deleted, no `ward` text value was changed, and no record was created for a location this project has no basis to assert.

## Users (`users.ward` -> operational `ward_id` etc, NOT `home_ward_id`)

108 worker row(s) with a ward set, 51 distinct value(s).

| ward text | rows affected | outcome |
|---|---|---|
| `Agaganj` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Alaipura` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Asansol (M Corp.) - Ward No.1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Asansol (M Corp.) - Ward No.10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Asansol (M Corp.) - Ward No.100` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Bagahada` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Howrah (M Corp) - Ward No.1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Howrah (M Corp) - Ward No.10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Howrah (M Corp) - Ward No.11` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Kanpur (M Corp.) - Ward No.1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Kanpur (M Corp.) - Ward No.10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Kanpur (M Corp.) - Ward No.100` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Mangalore (M Corp.) - Ward No.1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Mangalore (M Corp.) - Ward No.10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Mangalore (M Corp.) - Ward No.11` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Mysore (M Corp.) - Ward No.1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Mysore (M Corp.) - Ward No.10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Mysore (M Corp.) - Ward No.11` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `NMC Prabhag No-1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `NMC Prabhag No-10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `NMC Prabhag No-11` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Pune (M Corp) Ward No. 1 Kalas - Dhanori` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Pune (M Corp) Ward No. 2 Phulenagar- Nagpurchal` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Pune (M Corp) Ward No. 3 Vimannagar - Somnath Nagar` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Surat (M Corp.) - Ward No.1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Surat (M Corp.) - Ward No.10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Surat (M Corp.) - Ward No.11` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Vadodara (M Corp.) - Ward No.1` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Vadodara (M Corp.) - Ward No.10` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Vadodara (M Corp.) - Ward No.11` | 2 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Ward 1 — Chowk, Lucknow` | 2 | MATCHED -> ward_id=74956 (state_id=26, district_id=603, ulb_id=3985) |
| `Ward 103 — White Field, Bengaluru` | 2 | MATCHED -> ward_id=24749 (state_id=11, district_id=6, ulb_id=6) |
| `Ward 11 — Navrangpura, Ahmedabad` | 2 | MATCHED -> ward_id=4 (state_id=7, district_id=4, ulb_id=4) |
| `Ward 125 — Mylapore, Chennai` | 2 | MATCHED -> ward_id=67718 (state_id=23, district_id=474, ulb_id=2819) |
| `Ward 17 — Hazratganj, Lucknow` | 2 | MATCHED -> ward_id=80547 (state_id=26, district_id=603, ulb_id=3985) |
| `Ward 174 — Adyar, Chennai` | 2 | MATCHED -> ward_id=65794 (state_id=23, district_id=474, ulb_id=2819) |
| `Ward 174 — Koramangala, Bengaluru` | 2 | MATCHED -> ward_id=23113 (state_id=11, district_id=6, ulb_id=6) |
| `Ward 25 — Egmore, Chennai` | 2 | MATCHED -> ward_id=57605 (state_id=23, district_id=474, ulb_id=2819) |
| `Ward 3 — Indiranagar, Bengaluru` | 2 | MATCHED -> ward_id=6 (state_id=11, district_id=6, ulb_id=6) |
| `Ward 30 — Paldi, Ahmedabad` | 2 | MATCHED -> ward_id=13929 (state_id=7, district_id=4, ulb_id=4) |
| `Ward 37 — Maninagar, Ahmedabad` | 2 | MATCHED -> ward_id=13765 (state_id=7, district_id=4, ulb_id=4) |
| `Ward 42 — Malad, Mumbai` | 2 | MATCHED -> ward_id=37674 (state_id=14, district_id=359, ulb_id=1909) |
| `Ward 45 — BBD Bagh, Kolkata` | 2 | MATCHED -> ward_id=89852 (state_id=28, district_id=5, ulb_id=5) |
| `Ward 6 — Salt Lake, Kolkata` | 2 | MATCHED -> ward_id=5 (state_id=28, district_id=5, ulb_id=5) |
| `Ward 70 — Andheri West, Mumbai` | 2 | MATCHED -> ward_id=40023 (state_id=14, district_id=359, ulb_id=1909) |
| `Ward 80 — Kidderpore, Kolkata` | 2 | MATCHED -> ward_id=88687 (state_id=28, district_id=5, ulb_id=5) |
| `Ward 85 — Andheri East, Mumbai` | 2 | MATCHED -> ward_id=42944 (state_id=14, district_id=359, ulb_id=1909) |
| `Ward 89 — Aliganj, Lucknow` | 2 | MATCHED -> ward_id=83346 (state_id=26, district_id=603, ulb_id=3985) |
| `Ward No-01` | 4 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Ward No-02` | 4 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |
| `Ward No-03` | 4 | UNMAPPED -- no city recorded anywhere in the project for this ward string; not guessed |

## Complaints (`complaints.ward` -> structured location columns)

58 complaint row(s) with a ward set, 18 distinct value(s).

| ward text | rows affected | outcome |
|---|---|---|
| `Ward 1 — Chowk, Lucknow` | 6 | MATCHED -> ward_id=74956 (state_id=26, district_id=603, ulb_id=3985) |
| `Ward 103 — White Field, Bengaluru` | 1 | MATCHED -> ward_id=24749 (state_id=11, district_id=6, ulb_id=6) |
| `Ward 11 — Navrangpura, Ahmedabad` | 6 | MATCHED -> ward_id=4 (state_id=7, district_id=4, ulb_id=4) |
| `Ward 125 — Mylapore, Chennai` | 1 | MATCHED -> ward_id=67718 (state_id=23, district_id=474, ulb_id=2819) |
| `Ward 17 — Hazratganj, Lucknow` | 1 | MATCHED -> ward_id=80547 (state_id=26, district_id=603, ulb_id=3985) |
| `Ward 174 — Adyar, Chennai` | 1 | MATCHED -> ward_id=65794 (state_id=23, district_id=474, ulb_id=2819) |
| `Ward 174 — Koramangala, Bengaluru` | 1 | MATCHED -> ward_id=23113 (state_id=11, district_id=6, ulb_id=6) |
| `Ward 25 — Egmore, Chennai` | 6 | MATCHED -> ward_id=57605 (state_id=23, district_id=474, ulb_id=2819) |
| `Ward 3 — Indiranagar, Bengaluru` | 16 | MATCHED -> ward_id=6 (state_id=11, district_id=6, ulb_id=6) |
| `Ward 30 — Paldi, Ahmedabad` | 1 | MATCHED -> ward_id=13929 (state_id=7, district_id=4, ulb_id=4) |
| `Ward 37 — Maninagar, Ahmedabad` | 1 | MATCHED -> ward_id=13765 (state_id=7, district_id=4, ulb_id=4) |
| `Ward 42 — Malad, Mumbai` | 6 | MATCHED -> ward_id=37674 (state_id=14, district_id=359, ulb_id=1909) |
| `Ward 45 — BBD Bagh, Kolkata` | 1 | MATCHED -> ward_id=89852 (state_id=28, district_id=5, ulb_id=5) |
| `Ward 6 — Salt Lake, Kolkata` | 6 | MATCHED -> ward_id=5 (state_id=28, district_id=5, ulb_id=5) |
| `Ward 70 — Andheri West, Mumbai` | 1 | MATCHED -> ward_id=40023 (state_id=14, district_id=359, ulb_id=1909) |
| `Ward 80 — Kidderpore, Kolkata` | 1 | MATCHED -> ward_id=88687 (state_id=28, district_id=5, ulb_id=5) |
| `Ward 85 — Andheri East, Mumbai` | 1 | MATCHED -> ward_id=42944 (state_id=14, district_id=359, ulb_id=1909) |
| `Ward 89 — Aliganj, Lucknow` | 1 | MATCHED -> ward_id=83346 (state_id=26, district_id=603, ulb_id=3985) |
