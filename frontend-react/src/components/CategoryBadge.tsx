import { useUiLang } from "../lib/uiLang";
import { SERVICE_CATEGORY_DEFS, serviceCategoryLabel } from "../lib/serviceCategories";
import type { ServiceCategory } from "../lib/ragTypes";
import type { LangCode } from "../lib/i18n";

/** Small colored pill showing which civic-service category a complaint belongs to.
 *
 * LIVE-REPORTED GAP: complaint lists never showed this at all, even though every complaint is
 * classified into one of the 4 categories at filing time (Ask Sarthi's own routing, or the
 * Report an Issue wizard's 3-layer classifier) -- see Complaint.service_category's own docstring
 * for why that classification used to be thrown away instead of stored. A citizen/worker/admin
 * had no way to tell "this complaint is about roads" from the list alone.
 *
 * Renders nothing for a complaint filed before this field existed, or where classification was
 * genuinely unsure -- never a fabricated guess. Reuses the same color tokens
 * (--service-<color>/-bg) the service cards on Home/My Area already use, so this reads as the
 * same visual language, not a new one. */
export default function CategoryBadge({
  category,
  lang: langOverride,
}: {
  category: ServiceCategory | null;
  lang?: LangCode;
}) {
  const { lang: uiLang } = useUiLang();
  const lang = langOverride ?? uiLang;
  if (!category) return null;
  const def = SERVICE_CATEGORY_DEFS.find((d) => d.id === category);
  const color = def?.color ?? "waste";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontSize: 11,
        fontWeight: 600,
        padding: "3px 8px",
        borderRadius: 999,
        background: `var(--service-${color}-bg)`,
        color: `var(--service-${color})`,
        whiteSpace: "nowrap",
      }}
    >
      {serviceCategoryLabel(lang, category)}
    </span>
  );
}
