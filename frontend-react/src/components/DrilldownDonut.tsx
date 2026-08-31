import { useEffect, useRef } from "react";

export interface DrilldownLeaf {
  key: string;
  label: string;
  color: string;
  n: number;
}
export interface DrilldownNode {
  key: string;
  label: string;
  color: string;
  total: number;
  children: DrilldownLeaf[];
}

interface Props {
  nodes: DrilldownNode[];
  grandTotal: number;
  totalLabel: string;
  backLabel: string;
  hintText: string;
  emptyText: string;
}

// LIVE-REPORTED BUG (originally on ServiceDonutPanel, the first user of this component): r=15.9/
// stroke-width=6.5 left the hole only ~103px across at this panel's 172px render size -- too
// tight for longer drilled-in labels ("Roads & Potholes"), which visibly crowded/overlapped the
// ring's own arc. r=17 (changed here and in the matching JSX circle + the dynamically-created one
// below) grows the hole to ~113px without changing the ring's outer footprint at all. CIRC is
// DERIVED from r, not an independent number -- changing r here automatically keeps every arc's
// stroke-dasharray percentage-correct, so this one constant is the only thing that needs updating
// for the circumference math (the r="17" attributes elsewhere are a separate, cosmetic change
// that must stay in sync with this by hand).
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

/** Generic zoom-drilldown donut -- click a top-level slice and it expands to fill the whole ring,
 * then cracks open into that node's own child breakdown (a second donut), mirroring the classic
 * charting-library "drilldown" pie/donut interaction. Extracted from ServiceDonutPanel (which
 * always drilled service -> status) so a second, real two-level breakdown -- the worker
 * dashboard's Resolution Rate card, which drills status -> service category, the INVERSE pairing
 * -- can reuse the exact same interaction and visual language instead of a hand-rolled
 * near-duplicate. The `.svc-*` CSS classes below predate this generic extraction (named for their
 * first, service-donut-only use) but are purely presentational (ring/legend/back-button shape)
 * and apply equally to any two-level donut, so they're reused as-is rather than renamed. */
export default function DrilldownDonut({ nodes, grandTotal, totalLabel, backLabel, hintText, emptyText }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const centerNRef = useRef<HTMLSpanElement>(null);
  const centerLblRef = useRef<HTMLSpanElement>(null);
  const backBtnRef = useRef<HTMLButtonElement>(null);
  const backHintRef = useRef<HTMLParagraphElement>(null);
  const crumbRef = useRef<HTMLSpanElement>(null);
  const animatingRef = useRef(false);

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

    function renderTopView(instant: boolean) {
      const els = ensureSliceEls(nodes.length);
      const arcs = layout(nodes.map((s) => ({ n: s.total })));
      els.forEach((el, i) => {
        const s = nodes[i];
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
      centerLbl.textContent = totalLabel;
      centerN.style.opacity = "1";
      centerLbl.style.opacity = "1";
      crumbEl.textContent = "";
      backBtn.classList.remove("show");
      backHint.style.opacity = "1";
      legendEl.style.opacity = "1";
      legendEl.innerHTML = "";
      nodes.forEach((s) => {
        const pct = grandTotal > 0 ? Math.round((s.total / grandTotal) * 100) : 0;
        const row = document.createElement("div");
        row.className = "svc-leg-item";
        row.innerHTML = `<span class="svc-leg-sw" style="background:${s.color}"></span><span class="svc-leg-lbl">${s.label}</span><span class="svc-leg-pct">${s.total} · ${pct}%</span><svg class="svc-leg-chev" width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        row.addEventListener("click", () => zoomInto(s.key));
        legendEl.appendChild(row);
      });
    }

    function zoomInto(nodeKey: string) {
      if (animatingRef.current) return;
      animatingRef.current = true;
      const node = nodes.find((s) => s.key === nodeKey)!;
      const els = [...svg!.querySelectorAll<SVGCircleElement>(".svc-slice")];

      els.forEach((el) => {
        if (el.dataset.key === nodeKey) {
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
        const els2 = ensureSliceEls(node.children.length);
        const arcs = layout(node.children);
        els2.forEach((el) => {
          el.style.transition = "none";
          el.setAttribute("stroke-dasharray", `${CIRC} 0`);
          el.setAttribute("stroke-dashoffset", "0");
          el.style.stroke = node.color;
          el.style.opacity = "1";
        });
        void svg!.getBoundingClientRect();
        requestAnimationFrame(() => {
          els2.forEach((el, i) => {
            const leaf = node.children[i];
            const arc = arcs[i];
            el.style.transition = "";
            el.classList.add("svc-leaf");
            el.style.stroke = leaf.color;
            el.dataset.key = leaf.key;
            const pct = node.total > 0 ? Math.round((leaf.n / node.total) * 100) : 0;
            setSliceTitle(el, `${leaf.label}: ${leaf.n} · ${pct}%`);
            el.setAttribute("stroke-dasharray", `${arc.len} ${CIRC - arc.len}`);
            el.setAttribute("stroke-dashoffset", `${-arc.start}`);
            el.onclick = null;
          });
        });

        centerN.textContent = String(node.total);
        centerLbl.textContent = node.label;
        centerN.style.opacity = "1";
        centerLbl.style.opacity = "1";
        crumbEl.textContent = node.label;
        backBtn.classList.add("show");

        legendEl.innerHTML = "";
        node.children.forEach((leaf) => {
          const pct = node.total > 0 ? Math.round((leaf.n / node.total) * 100) : 0;
          const row = document.createElement("div");
          row.className = "svc-leg-item svc-leg-static";
          row.innerHTML = `<span class="svc-leg-sw" style="background:${leaf.color}"></span><span class="svc-leg-lbl">${leaf.label}</span><span class="svc-leg-pct">${leaf.n} · ${pct}%</span>`;
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
        renderTopView(true);
        animatingRef.current = false;
      }, 540);
    }

    backBtn.onclick = zoomOut;
    renderTopView(true);

    return () => {
      backBtn.onclick = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, grandTotal, totalLabel]);

  return (
    <div className="admin-loc-ai-body">
      <div className="loc-crumbs">
        <button ref={backBtnRef} type="button" className="svc-back-btn">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
            <path d="M15 6l-6 6 6 6" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {backLabel}
        </button>
        <span className="svc-crumb-current" ref={crumbRef} />
      </div>

      {nodes.length === 0 ? (
        <div className="loc-empty">{emptyText}</div>
      ) : (
        <div className="svc-donut-body">
          <div className="svc-donut-stage">
            <div className="svc-donut-svg-wrap">
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
          <p className="svc-back-hint" ref={backHintRef}>{hintText}</p>
        </div>
      )}
    </div>
  );
}
