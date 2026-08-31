import { useMemo } from "react";
import { t, type LangCode } from "../lib/i18n";
import { SERVICE_CATEGORY_DEFS } from "../lib/serviceCategories";
import type { ServiceStatusCount } from "../lib/api";
import type { ServiceCategory } from "../lib/ragTypes";
import DrilldownDonut, { type DrilldownNode } from "./DrilldownDonut";

type StatusKey = "pending" | "assigned" | "accepted" | "in_progress" | "resolved";
const STATUS_ORDER: StatusKey[] = ["pending", "assigned", "accepted", "in_progress", "resolved"];
// Same mapping LocationHierarchyPanel's own StatusBar uses -- accepted and in_progress share one
// color (both read as "work is happening") everywhere else in this app, so this stays consistent.
const STATUS_COLOR: Record<StatusKey, string> = {
  pending: "var(--status-pending)",
  assigned: "var(--status-open)",
  accepted: "var(--status-progress)",
  in_progress: "var(--status-progress)",
  resolved: "var(--status-resolved)",
};

interface Props {
  rows: ServiceStatusCount[];
  lang: LangCode;
  statusLabel: (status: StatusKey) => string;
}

/** Zoom-drilldown "complaints by service" donut: service -> status. A thin wrapper around
 * DrilldownDonut (the shared zoom-morph interaction, extracted here once a second real two-level
 * breakdown -- the worker dashboard's Resolution Rate card, status -> service, the INVERSE
 * pairing -- needed the exact same behavior) -- this component's only job is turning
 * GET /complaints/by-service's flat rows into DrilldownDonut's generic node/leaf shape. */
export default function ServiceDonutPanel({ rows, lang, statusLabel }: Props) {
  const nodes = useMemo<DrilldownNode[]>(() => {
    const grouped: Partial<Record<ServiceCategory, Partial<Record<StatusKey, number>>>> = {};
    for (const row of rows) {
      const sc = row.service_category as ServiceCategory;
      const st = row.status as StatusKey;
      const bucket = (grouped[sc] ??= {});
      bucket[st] = (bucket[st] || 0) + row.total;
    }
    return SERVICE_CATEGORY_DEFS.map((def) => {
      const counts = grouped[def.id] || {};
      const total = STATUS_ORDER.reduce((a, k) => a + (counts[k] || 0), 0);
      const children = STATUS_ORDER.filter((k) => counts[k]).map((k) => ({
        key: k,
        label: statusLabel(k),
        color: STATUS_COLOR[k],
        n: counts[k]!,
      }));
      return { key: def.id, label: t(lang, def.titleKey), color: `var(--service-${def.color})`, total, children };
    }).filter((s) => s.total > 0);
  }, [rows, lang, statusLabel]);

  const grandTotal = useMemo(() => nodes.reduce((a, s) => a + s.total, 0), [nodes]);

  return (
    <DrilldownDonut
      nodes={nodes}
      grandTotal={grandTotal}
      totalLabel={t(lang, "common.totalComplaints")}
      backLabel={t(lang, "common.allServices")}
      hintText={t(lang, "common.serviceDonutHint")}
      emptyText={t(lang, "admin.noComplaints")}
    />
  );
}
