import { useEffect, useRef } from "react";

// Same circumference convention DrilldownDonut uses: r=15.9 makes the circle's real
// circumference ~99.9 SVG units, close enough to 100 that a stroke-dasharray value doubles
// directly as "percent of the ring" -- see that component's own CIRC comment for the fuller
// rationale (this ring is deliberately NOT built on top of DrilldownDonut itself, see this file's
// own docstring below, but reuses its exact animation technique by hand).
const CIRC = 100;
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

/** Worker dashboard's Resolution Rate card. Collapsed by default: a plain 2-slice ring, green =
 * resolved share of `totalCount`, gray = everything still open -- deliberately NOT the same
 * multi-color donut LOOK as the neighboring "Complaints by Service" card at rest (an earlier
 * version was exactly that, and reusing the same look right next to it read as
 * duplicate/confusing). Clicking the gray "open" slice DOES reuse that card's exact zoom-crack
 * animation technique, by hand (not by rendering through the shared DrilldownDonut component --
 * this ring's numbers don't fit that component's assumption that a node's children sum to the
 * SAME total used for its own top-level arc: the "open" slice is sized as `totalCount -
 * resolvedCount` so the collapsed ring's math matches the stat cards above exactly, but its
 * CHILDREN also include Rejected, which is real but not part of `totalCount` at all -- see the
 * openTotal/openCount distinction below). Same 520ms cubic-bezier slice-grow, then instant
 * same-color refill + one-frame-later crack into real proportions, same centerN/centerLbl/
 * backHint/legend fade choreography and timing as ServiceDonutPanel's own drilldown.
 *
 * Colors: amber/blue for Needs response/In Progress (this app's own existing colorblind-safe
 * chart-series palette), deliberately different from both the status colors and
 * ServiceDonutPanel's green/blue/orange/purple. Rejected uses a fixed red (#DC2626) rather than
 * the theme-varying `--status-critical` token -- LIVE-REPORTED: that token's dark-mode tint
 * (#F87171, deliberately lightened for contrast against a dark background, same as every other
 * status color) read as pale pink rather than red at this ring's stroke width, undermining the
 * very distinction from "In Progress" it exists to make. A single fixed value keeps Rejected
 * looking like an actual warning red in both themes -- a deliberate, narrow exception for this
 * specific legibility problem, not a precedent for other status colors. */
export default function ResolutionRateGauge({
  totalCount,
  resolvedCount,
  assignedCount,
  inProgressCount,
  rejectedCount,
  resolvedLabel,
  stillOpenLabel,
  needsResponseLabel,
  inProgressLabel,
  rejectedLabel,
  hintText,
  backLabel,
  openLabel,
  gaugeLabel,
}: {
  totalCount: number;
  resolvedCount: number;
  assignedCount: number;
  inProgressCount: number;
  rejectedCount: number;
  resolvedLabel: string;
  stillOpenLabel: string;
  needsResponseLabel: string;
  inProgressLabel: string;
  rejectedLabel: string;
  hintText: string;
  backLabel: string;
  openLabel: string;
  gaugeLabel: string;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const centerNRef = useRef<HTMLSpanElement>(null);
  const centerLblRef = useRef<HTMLSpanElement>(null);
  const backBtnRef = useRef<HTMLButtonElement>(null);
  const backHintRef = useRef<HTMLParagraphElement>(null);
  const crumbRef = useRef<HTMLSpanElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const ringBtnRef = useRef<HTMLButtonElement>(null);
  const animatingRef = useRef(false);
  const expandedRef = useRef(false);

  const NEEDS_RESPONSE_COLOR = "var(--chart-series-4)";
  const IN_PROGRESS_COLOR = "var(--chart-series-1)";
  const REJECTED_COLOR = "#DC2626";
  const OPEN_COLOR = "var(--line)";
  const GRAY_INK = "var(--ink-3)";

  const rate = totalCount > 0 ? Math.round((resolvedCount / totalCount) * 100) : 0;
  const openCount = totalCount - resolvedCount;
  // This ring's own total for the EXPANDED breakdown -- deliberately NOT `totalCount` and NOT
  // `openCount` either (see docstring above): Rejected is real but was never part of
  // `totalCount`'s 100% the way Needs response/In Progress are.
  const openTotal = assignedCount + inProgressCount + rejectedCount;

  useEffect(() => {
    const svgMaybe = svgRef.current;
    const centerNMaybe = centerNRef.current;
    const centerLblMaybe = centerLblRef.current;
    const backBtnMaybe = backBtnRef.current;
    const backHintMaybe = backHintRef.current;
    const crumbMaybe = crumbRef.current;
    const legendMaybe = legendRef.current;
    const ringBtnMaybe = ringBtnRef.current;
    if (!svgMaybe || !centerNMaybe || !centerLblMaybe || !backBtnMaybe || !backHintMaybe || !crumbMaybe || !legendMaybe || !ringBtnMaybe) return;
    const svg = svgMaybe;
    const centerN = centerNMaybe;
    const centerLbl = centerLblMaybe;
    const backBtn = backBtnMaybe;
    const backHint = backHintMaybe;
    const crumbEl = crumbMaybe;
    const legendEl = legendMaybe;
    const ringBtn = ringBtnMaybe;

    // Same hover-tooltip approach DrilldownDonut's setSliceTitle uses -- reuses a single <title>
    // child per circle across re-renders (checked via querySelector first) rather than always
    // appending a new one, since these circle elements are recycled by ensureSliceEls, not
    // recreated. LIVE-REPORTED gap this fixes: every other chart on this page (the trend chart's
    // points, the service donut's slices) already shows exact values on hover -- this ring's
    // collapsed 2-slice view was the one place with none.
    function setSliceTitle(el: SVGCircleElement, text: string) {
      let titleEl = el.querySelector<SVGTitleElement>("title");
      if (!titleEl) {
        titleEl = document.createElementNS(NS, "title");
        el.appendChild(titleEl);
      }
      titleEl.textContent = text;
    }

    function ensureSliceEls(n: number): SVGCircleElement[] {
      let els = [...svg.querySelectorAll<SVGCircleElement>(".svc-slice")];
      while (els.length < n) {
        const c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", "21");
        c.setAttribute("cy", "21");
        c.setAttribute("r", "15.9");
        c.setAttribute("fill", "transparent");
        c.setAttribute("stroke-width", "4");
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

    function renderCollapsed(instant: boolean) {
      const els = ensureSliceEls(2);
      const resolvedArc = { start: 0, len: totalCount > 0 ? (resolvedCount / totalCount) * CIRC : 0 };
      const openArc = { start: resolvedArc.len, len: CIRC - resolvedArc.len };
      const specs = [
        { key: "resolved", label: resolvedLabel, color: "var(--status-resolved)", arc: resolvedArc, n: resolvedCount, clickable: false },
        { key: "open", label: stillOpenLabel, color: OPEN_COLOR, arc: openArc, n: openCount, clickable: openCount > 0 },
      ];
      els.forEach((el, i) => {
        const s = specs[i];
        el.classList.remove("svc-leaf");
        el.style.stroke = s.color;
        el.dataset.key = s.key;
        if (instant) el.style.transition = "none";
        const pct = totalCount > 0 ? Math.round((s.n / totalCount) * 100) : 0;
        setSliceTitle(el, `${s.label}: ${s.n} · ${pct}%`);
        el.setAttribute("stroke-dasharray", `${s.arc.len} ${CIRC - s.arc.len}`);
        el.setAttribute("stroke-dashoffset", `${-s.arc.start}`);
        el.style.opacity = "1";
        if (instant) requestAnimationFrame(() => { el.style.transition = ""; });
        el.onclick = s.clickable ? zoomIn : null;
        el.style.cursor = s.clickable ? "pointer" : "default";
      });
      centerN.style.fontSize = "26px";
      centerN.textContent = `${rate}%`;
      centerLbl.textContent = `${resolvedCount} / ${totalCount}`;
      centerN.style.opacity = "1";
      centerLbl.style.opacity = "1";
      crumbEl.textContent = "";
      backBtn.classList.remove("show");
      backHint.style.opacity = "1";
      legendEl.style.opacity = "0";
      legendEl.innerHTML = "";
      ringBtn.style.cursor = openCount > 0 ? "pointer" : "default";
      ringBtn.onclick = openCount > 0 ? zoomIn : null;
      expandedRef.current = false;
    }

    function zoomIn() {
      if (animatingRef.current || expandedRef.current) return;
      animatingRef.current = true;
      const els = [...svg.querySelectorAll<SVGCircleElement>(".svc-slice")];
      els.forEach((el) => {
        if (el.dataset.key === "open") {
          el.setAttribute("stroke-dasharray", `${CIRC} 0`);
          el.setAttribute("stroke-dashoffset", "0");
        } else {
          el.setAttribute("stroke-dasharray", `0 ${CIRC}`);
          el.style.opacity = "0";
        }
        el.onclick = null;
      });
      ringBtn.onclick = null;
      centerN.style.opacity = "0";
      centerLbl.style.opacity = "0";
      backHint.style.opacity = "0";

      setTimeout(() => {
        const children = [
          { key: "assigned", label: needsResponseLabel, color: NEEDS_RESPONSE_COLOR, n: assignedCount },
          { key: "in_progress", label: inProgressLabel, color: IN_PROGRESS_COLOR, n: inProgressCount },
          { key: "rejected", label: rejectedLabel, color: REJECTED_COLOR, n: rejectedCount },
        ];
        const els2 = ensureSliceEls(children.length);
        const arcs = layout(children);
        els2.forEach((el) => {
          el.style.transition = "none";
          el.setAttribute("stroke-dasharray", `${CIRC} 0`);
          el.setAttribute("stroke-dashoffset", "0");
          el.style.stroke = OPEN_COLOR;
          el.style.opacity = "1";
        });
        void svg.getBoundingClientRect();
        requestAnimationFrame(() => {
          els2.forEach((el, i) => {
            const leaf = children[i];
            const arc = arcs[i];
            el.style.transition = "";
            el.classList.add("svc-leaf");
            el.style.stroke = leaf.color;
            el.dataset.key = leaf.key;
            const pct = openTotal > 0 ? Math.round((leaf.n / openTotal) * 100) : 0;
            setSliceTitle(el, `${leaf.label}: ${leaf.n} · ${pct}%`);
            el.setAttribute("stroke-dasharray", `${arc.len} ${CIRC - arc.len}`);
            el.setAttribute("stroke-dashoffset", `${-arc.start}`);
            el.onclick = null;
          });
        });

        centerN.style.fontSize = "22px";
        centerN.textContent = String(openTotal);
        centerLbl.textContent = openLabel;
        centerN.style.opacity = "1";
        centerLbl.style.opacity = "1";
        crumbEl.textContent = stillOpenLabel;
        backBtn.classList.add("show");

        legendEl.innerHTML = "";
        children.forEach((leaf) => {
          const pct = openTotal > 0 ? Math.round((leaf.n / openTotal) * 100) : 0;
          const row = document.createElement("div");
          row.className = "svc-leg-item svc-leg-static";
          row.innerHTML = `<span class="svc-leg-sw" style="background:${leaf.color}"></span><span class="svc-leg-lbl">${leaf.label}</span><span class="svc-leg-pct">${leaf.n} · ${pct}%</span>`;
          legendEl.appendChild(row);
        });
        legendEl.style.opacity = "1";
        expandedRef.current = true;
        animatingRef.current = false;
      }, 540);
    }

    function zoomOut() {
      if (animatingRef.current || !expandedRef.current) return;
      animatingRef.current = true;
      const els = [...svg.querySelectorAll<SVGCircleElement>(".svc-slice")];
      els.forEach((el) => {
        el.setAttribute("stroke-dasharray", `${CIRC} 0`);
        el.setAttribute("stroke-dashoffset", "0");
      });
      centerN.style.opacity = "0";
      centerLbl.style.opacity = "0";
      legendEl.style.opacity = "0";
      backBtn.classList.remove("show");

      setTimeout(() => {
        renderCollapsed(true);
        animatingRef.current = false;
      }, 540);
    }

    backBtn.onclick = zoomOut;
    renderCollapsed(true);

    return () => {
      backBtn.onclick = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalCount, resolvedCount, assignedCount, inProgressCount, rejectedCount, resolvedLabel, stillOpenLabel, needsResponseLabel, inProgressLabel, rejectedLabel, openLabel]);

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, width: "100%" }}>
      <button
        ref={ringBtnRef}
        type="button"
        aria-label={hintText}
        style={{ position: "relative", width: 172, height: 172, flexShrink: 0, background: "none", border: "none", padding: 0 }}
      >
        <svg width="172" height="172" viewBox="0 0 42 42" ref={svgRef} role="img" aria-label={gaugeLabel}>
          <circle cx="21" cy="21" r="15.9" fill="none" stroke="var(--line)" strokeWidth="4" opacity="0" />
        </svg>
        {/* No repeated "Resolution rate" text here -- the card this sits inside already has that
            as its <h6> title (see WorkerDashboard.tsx); `gaugeLabel` above is only the SVG's
            aria-label for a screen reader, which has no visible sibling to read off of. */}
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center" }}>
          <span ref={centerNRef} style={{ fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1, transition: "opacity 200ms ease" }} />
          <span ref={centerLblRef} className="mono" style={{ fontSize: 11, color: GRAY_INK, marginTop: 6, transition: "opacity 200ms ease" }} />
        </div>
      </button>

      <div className="loc-crumbs" style={{ justifyContent: "center", marginBottom: 0 }}>
        <button ref={backBtnRef} type="button" className="svc-back-btn">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
            <path d="M15 6l-6 6 6 6" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {backLabel}
        </button>
        <span className="svc-crumb-current" ref={crumbRef} />
      </div>
      <div ref={legendRef} style={{ display: "flex", flexDirection: "column", gap: 4, width: "100%", maxWidth: 220, transition: "opacity 200ms ease" }} />
      <p ref={backHintRef} className="svc-back-hint" style={{ margin: 0, transition: "opacity 200ms ease" }}>{hintText}</p>
    </div>
  );
}
