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

/** The "Area" step (Locality.name) -- only 6 real locality rows exist today (confirmed directly
 * against production), all real, well-known neighborhood names, so translating them is a
 * transliteration exercise, not a guess -- unlike Ward below, there's no risk here worth leaving
 * untranslated. Extend as more localities get seeded. */
const LOCALITY_NAME_TRANSLATIONS: Record<string, Partial<Record<LangCode, string>>> = {
  "Civil Lines": { hi: "सिविल लाइन्स", mr: "सिव्हिल लाइन्स", or: "ସିଭିଲ ଲାଇନ୍ସ", gu: "સિવિલ લાઇન્સ", bn: "সিভিল লাইনস" },
  Indiranagar: { hi: "इंदिरानगर", mr: "इंदिरानगर", or: "ଇନ୍ଦିରାନଗର", gu: "ઈન્દિરાનગર", bn: "ইন্দিরানগর" },
  Kothrud: { hi: "कोथरुड", mr: "कोथरूड", or: "କୋଥ୍ରୁଡ଼", gu: "કોથરુડ", bn: "কোথরুড" },
  Navrangpura: { hi: "नवरंगपुरा", mr: "नवरंगपुरा", or: "ନବରଙ୍ଗପୁରା", gu: "નવરંગપુરા", bn: "নবরংগপুরা" },
  "Saheed Nagar": { hi: "शहीद नगर", mr: "शहीद नगर", or: "ଶହୀଦ ନଗର", gu: "શહીદ નગર", bn: "শহীদ নগর" },
  "Salt Lake": { hi: "साल्ट लेक", mr: "साल्ट लेक", or: "ସଲ୍ଟ ଲେକ", gu: "સોલ્ટ લેક", bn: "সল্ট লেক" },
};

/** Translates a real locality ("Area" step) name for display, falling back to the raw (English)
 * name for any locality not yet in the table above -- same reasoning as localizeStateName. */
export function localizeLocalityName(name: string, lang: LangCode): string {
  return LOCALITY_NAME_TRANSLATIONS[name]?.[lang] ?? name;
}

/** LIVE-REPORTED: the "Ward" step (Ward.name) had the same gap too. Unlike State/City/Area, real
 * ward names aren't clean proper nouns -- they're formal municipal-corporation ward designations
 * with wildly inconsistent formatting per city ("Chennai (M Corp.) - Ward No.125", "NMC Prabhag
 * No-1", "Ward No.174 (Koramangala)", "Pune (M Corp) Ward No. 1 Kalas - Dhanori", bare "Ward 11",
 * or, in Varanasi's case, just the neighborhood name with no "Ward" word at all). India has
 * 90,000+ real wards, far too many to ever hand-translate exhaustively -- so this table covers
 * only the exact, real ward strings that have an actual worker assigned today (confirmed directly
 * against the production database: `SELECT DISTINCT w.name FROM wards w JOIN users u ON
 * u.ward_id = w.id WHERE u.role = 'worker'`, 51 distinct real strings across 18 cities). Every
 * number keeps its original digits, except Bengali, which already uses its own numeral script
 * elsewhere in this file's sibling WARD_NAME_TRANSLATIONS-style tables (see
 * LocationHierarchyPanel.tsx's own history) -- kept consistent here for the same reason. A ward
 * seeded later that isn't one of these 51 falls back to the raw English name, same as every other
 * table in this file, rather than a guessed/garbled composition. */
const WARD_NAME_TRANSLATIONS: Record<string, Partial<Record<LangCode, string>>> = {
  "Maninagar, Ahmedabad (M.Corp.) Ward No. 37": {
    hi: "मणिनगर, अहमदाबाद (नगर निगम) वार्ड नं. 37",
    mr: "मणिनगर, अहमदाबाद (महानगरपालिका) वॉर्ड क्र. 37",
    or: "ମଣିନଗର, ଅହମ୍ମଦାବାଦ (ପୌର ନିଗମ) ୱାର୍ଡ ନଂ. 37",
    gu: "મણિનગર, અમદાવાદ (મ્યુનિસિપલ કોર્પોરેશન) વોર્ડ નં. 37",
    bn: "মণিনগর, আহমেদাবাদ (পৌরনিগম) ওয়ার্ড নং ৩৭",
  },
  "Paldi, Ahmedabad (M.Corp.) Ward No. 30": {
    hi: "पालडी, अहमदाबाद (नगर निगम) वार्ड नं. 30",
    mr: "पालडी, अहमदाबाद (महानगरपालिका) वॉर्ड क्र. 30",
    or: "ପାଲଡ଼ୀ, ଅହମ୍ମଦାବାଦ (ପୌର ନିଗମ) ୱାର୍ଡ ନଂ. 30",
    gu: "પાલડી, અમદાવાદ (મ્યુનિસિપલ કોર્પોરેશન) વોર્ડ નં. 30",
    bn: "পালডি, আহমেদাবাদ (পৌরনিগম) ওয়ার্ড নং ৩০",
  },
  "Ward 11": { hi: "वार्ड 11", mr: "वॉर्ड 11", or: "ୱାର୍ଡ 11", gu: "વોર્ડ 11", bn: "ওয়ার্ড ১১" },
  "Ward 3": { hi: "वार्ड 3", mr: "वॉर्ड 3", or: "ୱାର୍ଡ 3", gu: "વોર્ડ 3", bn: "ওয়ার্ড ৩" },
  "Ward 6": { hi: "वार्ड 6", mr: "वॉर्ड 6", or: "ୱାର୍ଡ 6", gu: "વોર્ડ 6", bn: "ওয়ার্ড ৬" },
  "Ward No.103 (White Field )": {
    hi: "वार्ड नं. 103 (व्हाइटफील्ड)", mr: "वॉर्ड क्र. 103 (व्हाइटफिल्ड)",
    or: "ୱାର୍ଡ ନଂ. 103 (ହ୍ୱାଇଟଫିଲ୍ଡ)", gu: "વોર્ડ નં. 103 (વ્હાઇટફિલ્ડ)", bn: "ওয়ার্ড নং ১০৩ (হোয়াইটফিল্ড)",
  },
  "Ward No.174 (Koramangala)": {
    hi: "वार्ड नं. 174 (कोरमंगला)", mr: "वॉर्ड क्र. 174 (कोरमंगला)",
    or: "ୱାର୍ଡ ନଂ. 174 (କୋରାମାଙ୍ଗାଲା)", gu: "વોર્ડ નં. 174 (કોરમંગલા)", bn: "ওয়ার্ড নং ১৭৪ (কোরামঙ্গলা)",
  },
  "Chennai (M Corp.) - Ward No.125": {
    hi: "चेन्नई (नगर निगम) - वार्ड नं. 125", mr: "चेन्नई (महानगरपालिका) - वॉर्ड क्र. 125",
    or: "ଚେନ୍ନାଇ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 125", gu: "ચેન્નાઈ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 125",
    bn: "চেন্নাই (পৌরনিগম) - ওয়ার্ড নং ১২৫",
  },
  "Chennai (M Corp.) - Ward No.25": {
    hi: "चेन्नई (नगर निगम) - वार्ड नं. 25", mr: "चेन्नई (महानगरपालिका) - वॉर्ड क्र. 25",
    or: "ଚେନ୍ନାଇ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 25", gu: "ચેન્નાઈ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 25",
    bn: "চেন্নাই (পৌরনিগম) - ওয়ার্ড নং ২৫",
  },
  "Chennai (M. Corp.) - Ward No. 174": {
    hi: "चेन्नई (नगर निगम) - वार्ड नं. 174", mr: "चेन्नई (महानगरपालिका) - वॉर्ड क्र. 174",
    or: "ଚେନ୍ନାଇ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 174", gu: "ચેન્નાઈ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 174",
    bn: "চেন্নাই (পৌরনিগম) - ওয়ার্ড নং ১৭৪",
  },
  "Ward No-01": { hi: "वार्ड नं. 01", mr: "वॉर्ड क्र. 01", or: "ୱାର୍ଡ ନଂ. 01", gu: "વોર્ડ નં. 01", bn: "ওয়ার্ড নং ০১" },
  "Ward No-02": { hi: "वार्ड नं. 02", mr: "वॉर्ड क्र. 02", or: "ୱାର୍ଡ ନଂ. 02", gu: "વોર્ડ નં. 02", bn: "ওয়ার্ড নং ০২" },
  "Ward No-03": { hi: "वार्ड नं. 03", mr: "वॉर्ड क्र. 03", or: "ୱାର୍ଡ ନଂ. 03", gu: "વોર્ડ નં. 03", bn: "ওয়ার্ড নং ০৩" },
  "Mangalore (M Corp.) - Ward No.1": {
    hi: "मंगलौर (नगर निगम) - वार्ड नं. 1", mr: "मंगळूर (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "ମାଙ୍ଗାଲୋର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "મેંગલોર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "ম্যাঙ্গালোর (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Mangalore (M Corp.) - Ward No.10": {
    hi: "मंगलौर (नगर निगम) - वार्ड नं. 10", mr: "मंगळूर (महानगरपालिका) - वॉर्ड क्र. 10",
    or: "ମାଙ୍ଗାଲୋର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 10", gu: "મેંગલોર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 10",
    bn: "ম্যাঙ্গালোর (পৌরনিগম) - ওয়ার্ড নং ১০",
  },
  "Mangalore (M Corp.) - Ward No.11": {
    hi: "मंगलौर (नगर निगम) - वार्ड नं. 11", mr: "मंगळूर (महानगरपालिका) - वॉर्ड क्र. 11",
    or: "ମାଙ୍ଗାଲୋର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 11", gu: "મેંગલોર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 11",
    bn: "ম্যাঙ্গালোর (পৌরনিগম) - ওয়ার্ড নং ১১",
  },
  "Howrah (M Corp) - Ward No.1": {
    hi: "हावड़ा (नगर निगम) - वार्ड नं. 1", mr: "हावडा (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "ହାଓଡ଼ା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "હાવડા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "হাওড়া (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Howrah (M Corp) - Ward No.10": {
    hi: "हावड़ा (नगर निगम) - वार्ड नं. 10", mr: "हावडा (महानगरपालिका) - वॉर्ड क्र. 10",
    or: "ହାଓଡ଼ା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 10", gu: "હાવડા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 10",
    bn: "হাওড়া (পৌরনিগম) - ওয়ার্ড নং ১০",
  },
  "Howrah (M Corp) - Ward No.11": {
    hi: "हावड़ा (नगर निगम) - वार्ड नं. 11", mr: "हावडा (महानगरपालिका) - वॉर्ड क्र. 11",
    or: "ହାଓଡ଼ା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 11", gu: "હાવડા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 11",
    bn: "হাওড়া (পৌরনিগম) - ওয়ার্ড নং ১১",
  },
  "Kanpur (M Corp.) - Ward No.1": {
    hi: "कानपुर (नगर निगम) - वार्ड नं. 1", mr: "कानपूर (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "କାନପୁର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "કાનપુર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "কানপুর (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Kanpur (M Corp.) - Ward No.10": {
    hi: "कानपुर (नगर निगम) - वार्ड नं. 10", mr: "कानपूर (महानगरपालिका) - वॉर्ड क्र. 10",
    or: "କାନପୁର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 10", gu: "કાનપુર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 10",
    bn: "কানপুর (পৌরনিগম) - ওয়ার্ড নং ১০",
  },
  "Kanpur (M Corp.) - Ward No.100": {
    hi: "कानपुर (नगर निगम) - वार्ड नं. 100", mr: "कानपूर (महानगरपालिका) - वॉर्ड क्र. 100",
    or: "କାନପୁର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 100", gu: "કાનપુર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 100",
    bn: "কানপুর (পৌরনিগম) - ওয়ার্ড নং ১০০",
  },
  "Kolkata (M Corp.) - Ward No.45": {
    hi: "कोलकाता (नगर निगम) - वार्ड नं. 45", mr: "कोलकाता (महानगरपालिका) - वॉर्ड क्र. 45",
    or: "କୋଲକାତା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 45", gu: "કોલકાતા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 45",
    bn: "কলকাতা (পৌরনিগম) - ওয়ার্ড নং ৪৫",
  },
  "Kolkata (M Corp.) - Ward No.80": {
    hi: "कोलकाता (नगर निगम) - वार्ड नं. 80", mr: "कोलकाता (महानगरपालिका) - वॉर्ड क्र. 80",
    or: "କୋଲକାତା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 80", gu: "કોલકાતા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 80",
    bn: "কলকাতা (পৌরনিগম) - ওয়ার্ড নং ৮০",
  },
  "Lucknow (M Corp.) - Ward No.1": {
    hi: "लखनऊ (नगर निगम) - वार्ड नं. 1", mr: "लखनौ (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "ଲକ୍ଷ୍ନୌ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "લખનૌ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "লখনউ (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Lucknow (M Corp.) - Ward No.17": {
    hi: "लखनऊ (नगर निगम) - वार्ड नं. 17", mr: "लखनौ (महानगरपालिका) - वॉर्ड क्र. 17",
    or: "ଲକ୍ଷ୍ନୌ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 17", gu: "લખનૌ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 17",
    bn: "লখনউ (পৌরনিগম) - ওয়ার্ড নং ১৭",
  },
  "Lucknow (M Corp.) - Ward No.89": {
    hi: "लखनऊ (नगर निगम) - वार्ड नं. 89", mr: "लखनौ (महानगरपालिका) - वॉर्ड क्र. 89",
    or: "ଲକ୍ଷ୍ନୌ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 89", gu: "લખનૌ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 89",
    bn: "লখনউ (পৌরনিগম) - ওয়ার্ড নং ৮৯",
  },
  "K/East - Ward No. 85": {
    hi: "के/ईस्ट - वार्ड नं. 85", mr: "के/ईस्ट - वॉर्ड क्र. 85", or: "କେ/ଇଷ୍ଟ - ୱାର୍ଡ ନଂ. 85",
    gu: "કે/ઈસ્ટ - વોર્ડ નં. 85", bn: "কে/ইস্ট - ওয়ার্ড নং ৮৫",
  },
  "K/West - Ward No. 70": {
    hi: "के/वेस्ट - वार्ड नं. 70", mr: "के/वेस्ट - वॉर्ड क्र. 70", or: "କେ/ୱେଷ୍ଟ - ୱାର୍ଡ ନଂ. 70",
    gu: "કે/વેસ્ટ - વોર્ડ નં. 70", bn: "কে/ওয়েস্ট - ওয়ার্ড নং ৭০",
  },
  "P/North - Ward No. 42": {
    hi: "पी/नॉर्थ - वार्ड नं. 42", mr: "पी/नॉर्थ - वॉर्ड क्र. 42", or: "ପି/ନର୍ଥ - ୱାର୍ଡ ନଂ. 42",
    gu: "પી/નોર્થ - વોર્ડ નં. 42", bn: "পি/নর্থ - ওয়ার্ড নং ৪২",
  },
  "Mysore (M Corp.) - Ward No.1": {
    hi: "मैसूर (नगर निगम) - वार्ड नं. 1", mr: "म्हैसूर (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "ମାଇସୋର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "મૈસુર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "মহীশূর (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Mysore (M Corp.) - Ward No.10": {
    hi: "मैसूर (नगर निगम) - वार्ड नं. 10", mr: "म्हैसूर (महानगरपालिका) - वॉर्ड क्र. 10",
    or: "ମାଇସୋର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 10", gu: "મૈસુર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 10",
    bn: "মহীশূর (পৌরনিগম) - ওয়ার্ড নং ১০",
  },
  "Mysore (M Corp.) - Ward No.11": {
    hi: "मैसूर (नगर निगम) - वार्ड नं. 11", mr: "म्हैसूर (महानगरपालिका) - वॉर्ड क्र. 11",
    or: "ମାଇସୋର (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 11", gu: "મૈસુર (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 11",
    bn: "মহীশূর (পৌরনিগম) - ওয়ার্ড নং ১১",
  },
  "NMC Prabhag No-1": {
    hi: "एनएमसी प्रभाग क्र-1", mr: "एनएमसी प्रभाग क्र-1", or: "ଏନଏମସି ପ୍ରଭାଗ ନଂ-1",
    gu: "એનએમસી પ્રભાગ નં-1", bn: "এনএমসি প্রভাগ নং-১",
  },
  "NMC Prabhag No-10": {
    hi: "एनएमसी प्रभाग क्र-10", mr: "एनएमसी प्रभाग क्र-10", or: "ଏନଏମସି ପ୍ରଭାଗ ନଂ-10",
    gu: "એનએમસી પ્રભાગ નં-10", bn: "এনএমসি প্রভাগ নং-১০",
  },
  "NMC Prabhag No-11": {
    hi: "एनएमसी प्रभाग क्र-11", mr: "एनएमसी प्रभाग क्र-11", or: "ଏନଏମସି ପ୍ରଭାଗ ନଂ-11",
    gu: "એનએમસી પ્રભાગ નં-11", bn: "এনএমসি প্রভাগ নং-১১",
  },
  "Asansol (M Corp.) - Ward No.1": {
    hi: "आसनसोल (नगर निगम) - वार्ड नं. 1", mr: "आसनसोल (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "ଆସନସୋଲ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "આસનસોલ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "আসানসোল (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Asansol (M Corp.) - Ward No.10": {
    hi: "आसनसोल (नगर निगम) - वार्ड नं. 10", mr: "आसनसोल (महानगरपालिका) - वॉर्ड क्र. 10",
    or: "ଆସନସୋଲ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 10", gu: "આસનસોલ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 10",
    bn: "আসানসোল (পৌরনিগম) - ওয়ার্ড নং ১০",
  },
  "Asansol (M Corp.) - Ward No.100": {
    hi: "आसनसोल (नगर निगम) - वार्ड नं. 100", mr: "आसनसोल (महानगरपालिका) - वॉर्ड क्र. 100",
    or: "ଆସନସୋଲ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 100", gu: "આસનસોલ (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 100",
    bn: "আসানসোল (পৌরনিগম) - ওয়ার্ড নং ১০০",
  },
  "Pune (M Corp) Ward No. 1 Kalas - Dhanori": {
    hi: "पुणे (नगर निगम) वार्ड नं. 1 कळस - धानोरी", mr: "पुणे (महानगरपालिका) वॉर्ड क्र. 1 कळस - धानोरी",
    or: "ପୁଣେ (ପୌର ନିଗମ) ୱାର୍ଡ ନଂ. 1 କଳସ - ଧାନୋରି", gu: "પુણે (મ્યુનિસિપલ કોર્પોરેશન) વોર્ડ નં. 1 કળસ - ધાનોરી",
    bn: "পুনে (পৌরনিগম) ওয়ার্ড নং ১ কলস - ধানোরি",
  },
  "Pune (M Corp) Ward No. 2 Phulenagar- Nagpurchal": {
    hi: "पुणे (नगर निगम) वार्ड नं. 2 फुलेनगर - नागपूरचाळ", mr: "पुणे (महानगरपालिका) वॉर्ड क्र. 2 फुलेनगर - नागपूरचाळ",
    or: "ପୁଣେ (ପୌର ନିଗମ) ୱାର୍ଡ ନଂ. 2 ଫୁଲେନଗର - ନାଗପୁରଚାଲ", gu: "પુણે (મ્યુનિસિપલ કોર્પોરેશન) વોર્ડ નં. 2 ફૂલેનગર - નાગપુરચાલ",
    bn: "পুনে (পৌরনিগম) ওয়ার্ড নং ২ ফুলেনগর - নাগপুরচাল",
  },
  "Pune (M Corp) Ward No. 3 Vimannagar - Somnath Nagar": {
    hi: "पुणे (नगर निगम) वार्ड नं. 3 विमाननगर - सोमनाथ नगर", mr: "पुणे (महानगरपालिका) वॉर्ड क्र. 3 विमाननगर - सोमनाथ नगर",
    or: "ପୁଣେ (ପୌର ନିଗମ) ୱାର୍ଡ ନଂ. 3 ବିମାନନଗର - ସୋମନାଥ ନଗର", gu: "પુણે (મ્યુનિસિપલ કોર્પોરેશન) વોર્ડ નં. 3 વિમાનનગર - સોમનાથ નગર",
    bn: "পুনে (পৌরনিগম) ওয়ার্ড নং ৩ বিমাননগর - সোমনাথ নগর",
  },
  "Surat (M Corp.) - Ward No.1": {
    hi: "सूरत (नगर निगम) - वार्ड नं. 1", mr: "सूरत (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "ସୁରଟ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "સુરત (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "সুরাট (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Surat (M Corp.) - Ward No.10": {
    hi: "सूरत (नगर निगम) - वार्ड नं. 10", mr: "सूरत (महानगरपालिका) - वॉर्ड क्र. 10",
    or: "ସୁରଟ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 10", gu: "સુરત (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 10",
    bn: "সুরাট (পৌরনিগম) - ওয়ার্ড নং ১০",
  },
  "Surat (M Corp.) - Ward No.11": {
    hi: "सूरत (नगर निगम) - वार्ड नं. 11", mr: "सूरत (महानगरपालिका) - वॉर्ड क्र. 11",
    or: "ସୁରଟ (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 11", gu: "સુરત (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 11",
    bn: "সুরাট (পৌরনিগম) - ওয়ার্ড নং ১১",
  },
  "Vadodara (M Corp.) - Ward No.1": {
    hi: "वडोदरा (नगर निगम) - वार्ड नं. 1", mr: "वडोदरा (महानगरपालिका) - वॉर्ड क्र. 1",
    or: "ବରୋଦା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 1", gu: "વડોદરા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 1",
    bn: "ভদোদরা (পৌরনিগম) - ওয়ার্ড নং ১",
  },
  "Vadodara (M Corp.) - Ward No.10": {
    hi: "वडोदरा (नगर निगम) - वार्ड नं. 10", mr: "वडोदरा (महानगरपालिका) - वॉर्ड क्र. 10",
    or: "ବରୋଦା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 10", gu: "વડોદરા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 10",
    bn: "ভদোদরা (পৌরনিগম) - ওয়ার্ড নং ১০",
  },
  "Vadodara (M Corp.) - Ward No.11": {
    hi: "वडोदरा (नगर निगम) - वार्ड नं. 11", mr: "वडोदरा (महानगरपालिका) - वॉर्ड क्र. 11",
    or: "ବରୋଦା (ପୌର ନିଗମ) - ୱାର୍ଡ ନଂ. 11", gu: "વડોદરા (મ્યુનિસિપલ કોર્પોરેશન) - વોર્ડ નં. 11",
    bn: "ভদোদরা (পৌরনিগম) - ওয়ার্ড নং ১১",
  },
  Agaganj: { hi: "अगागंज", mr: "अगागंज", or: "ଆଗାଗଞ୍ଜ", gu: "અગાગંજ", bn: "আগাগঞ্জ" },
  Alaipura: { hi: "अलईपुरा", mr: "अलईपुरा", or: "ଅଲାଇପୁରା", gu: "અલાઈપુરા", bn: "আলাইপুরা" },
  Bagahada: { hi: "बगहाड़ा", mr: "बगहाडा", or: "ବଗହଡ଼ା", gu: "બગહાડા", bn: "বগহাড়া" },
};

/** Translates a real ward name for display, falling back to the raw (English) name for any ward
 * not yet in the table above -- same reasoning as localizeStateName. */
export function localizeWardName(name: string, lang: LangCode): string {
  return WARD_NAME_TRANSLATIONS[name]?.[lang] ?? name;
}
