import { useEffect, useMemo, useRef } from "react";
import { t, type LangCode } from "../lib/i18n";
import { SERVICE_CATEGORY_DEFS } from "../lib/serviceCategories";
import type { ServiceStatusCount } from "../lib/api";
import type { ServiceCategory } from "../lib/ragTypes";

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

interface StatusSeg {
  key: StatusKey;
  label: string;
  color: string;
  n: number;
}
interface ServiceNode {
  key: ServiceCategory;
  label: string;
  color: string;
  total: number;
  statuses: StatusSeg[];
}

// LIVE-REPORTED BUG: r=15.9/stroke-width=6.5 left the hole only ~103px across at this panel's
// 172px render size -- too tight for longer drilled-in labels ("Roads & Potholes"), which visibly
// crowded/overlapped the ring's own arc. r=17 (changed here and in the matching JSX circle + the
// dynamically-created one below) grows the hole to ~113px without changing the ring's outer
// footprint at all, so it doesn't reopen the row-height/gap work this card's size was tuned for.
// stroke-width was briefly thinned to 5 alongside this (for an even bigger ~119px hole) then
// reverted back to the original 6.5 on request -- r=17 alone still gives real, if smaller,
// breathing room over the original r=15.9 while keeping the ring's original visual thickness.
// CIRC is DERIVED from r, not an independent number -- changing r here automatically keeps every
// arc's stroke-dasharray percentage-correct, so this one constant is the only thing that needs
// updating for the circumference math (the r="17" attributes elsewhere are a separate, cosmetic
// change that must stay in sync with this by hand).
const CIRC = 2 * Math.PI * 17;
const NS = "http://www.w3.org/2000/svg";

function layout(ns: { n: number }[]): { start: number; len: number }[] {
  const total = ns.reduce((a, s) => a + s.n, 0) || 1;
  let offset = 0;
  return ns.map((s) => {
    const len = (s.n / total) * CIRC;
    const seg = { start: offset, len };
    offset += len;
    return seg;
  });
}

interface Props {
  rows: ServiceStatusCount[];
  lang: LangCode;
  statusLabel: (status: StatusKey) => string;
}

/** Zoom-drilldown "complaints by service" donut -- click a service slice and it expands to fill
 * the whole ring, then cracks open into that service's own status breakdown (a second donut),
 * mirroring the classic charting-library "drilldown" pie/donut interaction. Plays the same
 * structural role as LocationHierarchyPanel (a drill-down breakdown widget), just service ->
 * status here instead of state -> district -> ward, and a zoom-morph transition instead of a
 * clickable list -- see GET /complaints/by-service's own docstring for the data shape. */
export default function ServiceDonutPanel({ rows, lang, statusLabel }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const centerNRef = useRef<HTMLSpanElement>(null);
  const centerLblRef = useRef<HTMLSpanElement>(null);
  const backBtnRef = useRef<HTMLButtonElement>(null);
  const backHintRef = useRef<HTMLParagraphElement>(null);
  const crumbRef = useRef<HTMLSpanElement>(null);
  const animatingRef = useRef(false);

  const services = useMemo<ServiceNode[]>(() => {
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
      const statuses = STATUS_ORDER.filter((k) => counts[k]).map((k) => ({
        key: k,
        label: statusLabel(k),
        color: STATUS_COLOR[k],
        n: counts[k]!,
      }));
      return { key: def.id, label: t(lang, def.titleKey), color: `var(--service-${def.color})`, total, statuses };
    }).filter((s) => s.total > 0);
  }, [rows, lang, statusLabel]);

  const grandTotal = useMemo(() => services.reduce((a, s) => a + s.total, 0), [services]);

  useEffect(() => {
    const svgMaybe = svgRef.current;
    const legendMaybe = legendRef.current;
    const centerNMaybe = centerNRef.current;
    const centerLblMaybe = centerLblRef.current;
    const backBtnMaybe = backBtnRef.current;
    const backHintMaybe = backHintRef.current;
    const crumbMaybe = crumbRef.current;
    if (!svgMaybe || !legendMaybe || !centerNMaybe || !centerLblMaybe || !backBtnMaybe || !backHintMaybe || !crumbMaybe) return;
    // Re-bound to fresh consts so TS keeps them non-null inside the nested function declarations
    // below (control-flow narrowing from the guard above doesn't cross a function boundary).
    const svg = svgMaybe;
    const legendEl = legendMaybe;
    const centerN = centerNMaybe;
    const centerLbl = centerLblMaybe;
    const backBtn = backBtnMaybe;
    const backHint = backHintMaybe;
    const crumbEl = crumbMaybe;

    function ensureSliceEls(n: number): SVGCircleElement[] {
      let els = [...svg.querySelectorAll<SVGCircleElement>(".svc-slice")];
      while (els.length < n) {
        const c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", "21");
        c.setAttribute("cy", "21");
        c.setAttribute("r", "17");
        c.setAttribute("fill", "transparent");
        c.setAttribute("stroke-width", "6.5");
        c.setAttribute("transform", "rotate(-90 21 21)");
        c.classList.add("svc-slice");
        svg.appendChild(c);
        els.push(c);
      }
      while (els.length > n) {
        els.pop()!.remove();
        els = [...svg.querySelectorAll<SVGCircleElement>(".svc-slice")];
      }
      return els;
    }

    // LIVE-REPORTED: every other chart in the app (AiHealthChart's line-chart points, the location
    // panel's own mini status bars) already shows a hover tooltip with the exact value -- this
    // ring was the one place with none, even though its slices are drawn as raw DOM circles (via
    // ensureSliceEls above), not JSX, so they can't just take a `<title>` child the declarative
    // way AiHealthChart's `<circle><title>...</title></circle>` does. Reuses a single `<title>`
    // child per circle across re-renders (checked via `querySelector` first) rather than always
    // appending a new one, since these circle ELEMENTS themselves are reused/recycled by
    // ensureSliceEls -- appending unconditionally would leave old, stale `<title>`s stacking up
    // underneath each new one on every render.
    function setSliceTitle(el: SVGCircleElement, text: string) {
      // TypeScript's tag-name-keyed querySelector overload maps the literal string "title" to
      // HTMLTitleElement (the <head><title> tag) by default -- there's no separate SVG-aware
      // overload for it, even though the element actually created/queried here is an SVG <title>
      // (SVGTitleElement, appended via createElementNS below). An explicit generic corrects the
      // inferred type to match what's actually on the page, rather than the wrong HTML one.
      let titleEl = el.querySelector<SVGTitleElement>("title");
      if (!titleEl) {
        titleEl = document.createElementNS(NS, "title");
        el.appendChild(titleEl);
      }
      titleEl.textContent = text;
    }

    function renderServicesView(instant: boolean) {
      const els = ensureSliceEls(services.length);
      const arcs = layout(services.map((s) => ({ n: s.total })));
      els.forEach((el, i) => {
        const s = services[i];
        const arc = arcs[i];
        el.classList.remove("svc-leaf");
        el.style.stroke = s.color;
        el.dataset.key = s.key;
        if (instant) el.style.transition = "none";
        const pct = grandTotal > 0 ? Math.round((s.total / grandTotal) * 100) : 0;
        setSliceTitle(el, `${s.label}: ${s.total} · ${pct}%`);
        el.setAttribute("stroke-dasharray", `${arc.len} ${CIRC - arc.len}`);
        el.setAttribute("stroke-dashoffset", `${-arc.start}`);
        el.style.opacity = "1";
        if (instant) requestAnimationFrame(() => { el.style.transition = ""; });
        el.onclick = () => zoomInto(s.key);
      });
      centerN.textContent = String(grandTotal);
      centerLbl.textContent = t(lang, "common.totalComplaints");
      centerN.style.opacity = "1";
      centerLbl.style.opacity = "1";
      crumbEl.textContent = "";
      backBtn.classList.remove("show");
      backHint.style.opacity = "1";
      legendEl.style.opacity = "1";
      legendEl.innerHTML = "";
      services.forEach((s) => {
        const pct = grandTotal > 0 ? Math.round((s.total / grandTotal) * 100) : 0;
        const row = document.createElement("div");
        row.className = "svc-leg-item";
        // width/height on this chevron are overridden by .svc-leg-chev's own CSS (dashboard.css)
        // the same way the donut ring's svg is -- kept in sync here anyway, same reasoning as
        // that rule's own comment.
        row.innerHTML = `<span class="svc-leg-sw" style="background:${s.color}"></span><span class="svc-leg-lbl">${s.label}</span><span class="svc-leg-pct">${s.total} · ${pct}%</span><svg class="svc-leg-chev" width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        row.addEventListener("click", () => zoomInto(s.key));
        legendEl.appendChild(row);
      });
    }

    function zoomInto(serviceKey: ServiceCategory) {
      if (animatingRef.current) return;
      animatingRef.current = true;
      const service = services.find((s) => s.key === serviceKey)!;
      const els = [...svg!.querySelectorAll<SVGCircleElement>(".svc-slice")];

      els.forEach((el) => {
        if (el.dataset.key === serviceKey) {
          el.setAttribute("stroke-dasharray", `${CIRC} 0`);
          el.setAttribute("stroke-dashoffset", "0");
        } else {
          el.setAttribute("stroke-dasharray", `0 ${CIRC}`);
          el.style.opacity = "0";
        }
        el.onclick = null;
      });
      centerN.style.opacity = "0";
      centerLbl.style.opacity = "0";
      legendEl.style.opacity = "0";
      legendEl.style.transition = "opacity 200ms ease";
      backHint.style.opacity = "0";

      setTimeout(() => {
        const els2 = ensureSliceEls(service.statuses.length);
        const arcs = layout(service.statuses);
        els2.forEach((el) => {
          el.style.transition = "none";
          el.setAttribute("stroke-dasharray", `${CIRC} 0`);
          el.setAttribute("stroke-dashoffset", "0");
          el.style.stroke = service.color;
          el.style.opacity = "1";
        });
        void svg!.getBoundingClientRect();
        requestAnimationFrame(() => {
          els2.forEach((el, i) => {
            const s = service.statuses[i];
            const arc = arcs[i];
            el.style.transition = "";
            el.classList.add("svc-leaf");
            el.style.stroke = s.color;
            el.dataset.key = s.key;
            const pct = service.total > 0 ? Math.round((s.n / service.total) * 100) : 0;
            setSliceTitle(el, `${s.label}: ${s.n} · ${pct}%`);
            el.setAttribute("stroke-dasharray", `${arc.len} ${CIRC - arc.len}`);
            el.setAttribute("stroke-dashoffset", `${-arc.start}`);
            el.onclick = null;
          });
        });

        centerN.textContent = String(service.total);
        centerLbl.textContent = service.label;
        centerN.style.opacity = "1";
        centerLbl.style.opacity = "1";
        crumbEl.textContent = service.label;
        backBtn.classList.add("show");

        legendEl.innerHTML = "";
        service.statuses.forEach((s) => {
          const pct = Math.round((s.n / service.total) * 100);
          const row = document.createElement("div");
          row.className = "svc-leg-item svc-leg-static";
          row.innerHTML = `<span class="svc-leg-sw" style="background:${s.color}"></span><span class="svc-leg-lbl">${s.label}</span><span class="svc-leg-pct">${s.n} · ${pct}%</span>`;
          legendEl.appendChild(row);
        });
        legendEl.style.opacity = "1";
        animatingRef.current = false;
      }, 540);
    }

    function zoomOut() {
      if (animatingRef.current) return;
      const els = [...svg!.querySelectorAll<SVGCircleElement>(".svc-slice")];
      if (els.length === 0 || !els[0].classList.contains("svc-leaf")) return;
      animatingRef.current = true;
      els.forEach((el) => {
        el.setAttribute("stroke-dasharray", `${CIRC} 0`);
        el.setAttribute("stroke-dashoffset", "0");
      });
      centerN.style.opacity = "0";
      centerLbl.style.opacity = "0";
      legendEl.style.opacity = "0";
      backHint.style.opacity = "0";
      backBtn.classList.remove("show");
      crumbEl.textContent = "";

      setTimeout(() => {
        renderServicesView(true);
        animatingRef.current = false;
      }, 540);
    }

    backBtn.onclick = zoomOut;
    renderServicesView(true);

    return () => {
      backBtn.onclick = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [services, grandTotal, lang]);

  return (
    <div className="admin-loc-ai-body">
      <div className="loc-crumbs">
        <button ref={backBtnRef} type="button" className="svc-back-btn">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
            <path d="M15 6l-6 6 6 6" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {t(lang, "common.allServices")}
        </button>
        <span className="svc-crumb-current" ref={crumbRef} />
      </div>

      {services.length === 0 ? (
        <div className="loc-empty">{t(lang, "admin.noComplaints")}</div>
      ) : (
        <div className="svc-donut-body">
          <div className="svc-donut-stage">
            <div className="svc-donut-svg-wrap">
              {/* width/height here are just a sensible intrinsic default before CSS loads -- the
                  real, authoritative size is set by .svc-donut-svg-wrap's own CSS (dashboard.css),
                  which forces this svg to 100%/100% of its wrapper specifically so the two can
                  never drift apart again (see that rule's own LIVE-REPORTED comment for the bug
                  this fixes: this attribute used to silently override the wrapper's smaller CSS
                  size). Keep this number in sync with .svc-donut-svg-wrap's width/height anyway --
                  don't rely on the CSS override alone to paper over a stale value here. */}
              <svg width="172" height="172" viewBox="0 0 42 42" ref={svgRef}>
                <circle cx="21" cy="21" r="17" fill="transparent" stroke="var(--surface-2)" strokeWidth="6.5" />
              </svg>
              <div className="svc-donut-center">
                <span className="svc-donut-n" ref={centerNRef} />
                <span className="svc-donut-lbl" ref={centerLblRef} />
              </div>
            </div>
            <div className="svc-donut-legend" ref={legendRef} />
          </div>
          <p className="svc-back-hint" ref={backHintRef}>{t(lang, "common.serviceDonutHint")}</p>
        </div>
      )}
    </div>
  );
}
