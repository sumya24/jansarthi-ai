import { t, type LangCode } from "./i18n";

// The timeline's from_status/to_status only ever take these 6 fixed backend status codes (see
// record_status_change()'s call sites, plus complaint_agent.py which sets the brand-new-complaint
// status "open" before the assignment system's first pass ever runs) -- a closed set that's
// already fully translated for every supported language in i18n.ts (the same labels
// ComplaintTracker's progress bar uses), so this is a plain lookup, not a translation-service call.
//
// LIVE-REPORTED: this lookup originally lived only inside ComplaintReportView.tsx, so the
// Resolution Report's own status timeline localized correctly while the plain on-page status
// timeline on CitizenComplaintDetail/WorkerComplaintDetail/AdminComplaintDetail rendered the
// raw backend codes ("open", "in_progress", ...) untranslated, directly below it -- confirmed
// live: a Marathi citizen saw "accepted → in_progress" in English on the same page whose own
// Resolution Report preview, just below it, correctly said "स्वीकारले → प्रगतीपथावर". Moved here
// so every status-history renderer shares the one lookup instead of only some of them having it.
const _STATUS_LABEL_KEYS: Record<string, string> = {
  open: "citizen.trackSubmitted",
  pending: "citizen.statusPending",
  assigned: "citizen.statusAssigned",
  accepted: "citizen.statusAccepted",
  in_progress: "citizen.trackInProgress",
  resolved: "citizen.statusResolved",
};

export function statusLabel(lang: LangCode, status: string): string {
  const key = _STATUS_LABEL_KEYS[status];
  return key ? t(lang, key) : status;
}
