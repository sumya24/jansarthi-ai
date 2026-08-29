import type { LangCode } from "./i18n";

/** Real Indian state names are stored in English in the `states` table and every state-selection
 * UI in the app (Signup's HomeLocationPicker, WorkerLocationPicker, AssignWorkerModal, the Admin
 * dashboard's location drill-down) renders them exactly as the API returns them. Only the states
 * that actually show up in today's real demo data are translated here -- India has 28 states + 8
 * union territories, and a hand-built table covering all 36 accurately (vs. an untranslated
 * fallback for the rest) isn't worth the risk of a wrong/invented name in a civic government app
 * for states no real account is in yet. Extend this table as more states get real users. */
const STATE_NAME_TRANSLATIONS: Record<string, Partial<Record<LangCode, string>>> = {
  Karnataka: { hi: "कर्नाटक", mr: "कर्नाटक", or: "କର୍ଣ୍ଣାଟକ", gu: "કર્ણાટક", bn: "কর্ণাটক" },
  Maharashtra: { hi: "महाराष्ट्र", mr: "महाराष्ट्र", or: "ମହାରାଷ୍ଟ୍ର", gu: "મહારાષ્ટ્ર", bn: "মহারাষ্ট্র" },
  Gujarat: { hi: "गुजरात", mr: "गुजरात", or: "ଗୁଜରାଟ", gu: "ગુજરાત", bn: "গুজরাট" },
  "Tamil Nadu": { hi: "तमिलनाडु", mr: "तामिळनाडू", or: "ତାମିଲନାଡୁ", gu: "તમિલનાડુ", bn: "তামিলনাড়ু" },
  "Uttar Pradesh": { hi: "उत्तर प्रदेश", mr: "उत्तर प्रदेश", or: "ଉତ୍ତର ପ୍ରଦେଶ", gu: "ઉત્તર પ્રદેશ", bn: "উত্তরপ্রদেশ" },
  "West Bengal": { hi: "पश्चिम बंगाल", mr: "पश्चिम बंगाल", or: "ପଶ୍ଚିମବଙ୍ଗ", gu: "પશ્ચિમ બંગાળ", bn: "পশ্চিমবঙ্গ" },
};

/** Translates a real Indian state name for display, falling back to the raw (English) name for
 * any state not yet in the table above -- an untranslated new state showing up is better than a
 * blank label or a guessed/wrong translation. */
export function localizeStateName(name: string, lang: LangCode): string {
  return STATE_NAME_TRANSLATIONS[name]?.[lang] ?? name;
}
