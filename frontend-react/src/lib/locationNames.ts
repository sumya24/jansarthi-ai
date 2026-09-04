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

/** LIVE-REPORTED: the "City" step of the same picker (Signup's HomeLocationPicker,
 * WorkerLocationPicker, AssignWorkerModal, the Admin dashboard's location drill-down) is really a
 * District row (see routes/locations.py's `list_cities` -- District.name is the citizen-
 * recognizable city name, e.g. "Pune", as opposed to ULB.name's formal civic-body name) and had
 * the exact same gap as the state dropdown: always rendered in raw English, even once the rest of
 * the UI switched language. Same anti-fabrication scoping as STATE_NAME_TRANSLATIONS above --
 * covers only the districts that actually have a real worker-backed ward under them today
 * (confirmed directly against the production database), not a guess at all ~750 of India's
 * districts. Extend this table as more districts get real users. */
const CITY_NAME_TRANSLATIONS: Record<string, Partial<Record<LangCode, string>>> = {
  Ahmedabad: { hi: "अहमदाबाद", mr: "अहमदाबाद", or: "ଅହମ୍ମଦାବାଦ", gu: "અમદાવાદ", bn: "আহমেদাবাদ" },
  "Alluri Sitharama Raju": {
    hi: "अल्लूरी सीताराम राजू", mr: "अल्लुरी सीताराम राजू", or: "ଆଲୁରୀ ସୀତାରାମ ରାଜୁ",
    gu: "અલ્લુરી સીતારામ રાજુ", bn: "আল্লুরি সীতারাম রাজু",
  },
  "Bengaluru Urban": {
    hi: "बेंगलुरु शहरी", mr: "बेंगळुरू शहरी", or: "ବେଙ୍ଗାଲୁରୁ ସହରାଞ୍ଚଳ",
    gu: "બેંગલુરુ શહેરી", bn: "বেঙ্গালুরু শহুরে",
  },
  Chennai: { hi: "चेन्नई", mr: "चेन्नई", or: "ଚେନ୍ନାଇ", gu: "ચેન્નાઈ", bn: "চেন্নাই" },
  Coimbatore: { hi: "कोयंबटूर", mr: "कोइंबतूर", or: "କୋଏମ୍ବାଟୁର", gu: "કોઈમ્બતુર", bn: "কোয়েম্বাটুর" },
  "Dakshina Kannada": {
    hi: "दक्षिण कन्नड़", mr: "दक्षिण कन्नड", or: "ଦକ୍ଷିଣ କନ୍ନଡ", gu: "દક્ષિણ કન્નડ", bn: "দক্ষিণ কন্নড়",
  },
  Howrah: { hi: "हावड़ा", mr: "हावडा", or: "ହାଓଡ଼ା", gu: "હાવડા", bn: "হাওড়া" },
  "Kanpur Nagar": { hi: "कानपुर नगर", mr: "कानपूर नगर", or: "କାନପୁର ନଗର", gu: "કાનપુર નગર", bn: "কানপুর নগর" },
  Kolkata: { hi: "कोलकाता", mr: "कोलकाता", or: "କୋଲକାତା", gu: "કોલકાતા", bn: "কলকাতা" },
  Lucknow: { hi: "लखनऊ", mr: "लखनौ", or: "ଲକ୍ଷ୍ନୌ", gu: "લખનૌ", bn: "লখনউ" },
  Madurai: { hi: "मदुरै", mr: "मदुराई", or: "ମାଦୁରାଇ", gu: "મદુરાઈ", bn: "মাদুরাই" },
  Mumbai: { hi: "मुंबई", mr: "मुंबई", or: "ମୁମ୍ବାଇ", gu: "મુંબઈ", bn: "মুম্বাই" },
  Mysuru: { hi: "मैसूरु", mr: "म्हैसूर", or: "ମାଇସୁରୁ", gu: "મૈસુરુ", bn: "মহীশূর" },
  Nagpur: { hi: "नागपुर", mr: "नागपूर", or: "ନାଗପୁର", gu: "નાગપુર", bn: "নাগপুর" },
  "Paschim Bardhaman": {
    hi: "पश्चिम बर्धमान", mr: "पश्चिम बर्धमान", or: "ପଶ୍ଚିମ ବର୍ଦ୍ଧମାନ",
    gu: "પશ્ચિમ બર્ધમાન", bn: "পশ্চিম বর্ধমান",
  },
  Pune: { hi: "पुणे", mr: "पुणे", or: "ପୁଣେ", gu: "પુણે", bn: "পুনে" },
  Surat: { hi: "सूरत", mr: "सूरत", or: "ସୁରଟ", gu: "સુરત", bn: "সুরাট" },
  Vadodara: { hi: "वडोदरा", mr: "वडोदरा", or: "ବରୋଦା", gu: "વડોદરા", bn: "ভদোদরা" },
  Varanasi: { hi: "वाराणसी", mr: "वाराणसी", or: "ବାରାଣାସୀ", gu: "વારાણસી", bn: "বারাণসী" },
};

/** Translates a real district ("City" step) name for display, falling back to the raw (English)
 * name for any district not yet in the table above -- same reasoning as localizeStateName. */
export function localizeCityName(name: string, lang: LangCode): string {
  return CITY_NAME_TRANSLATIONS[name]?.[lang] ?? name;
}
