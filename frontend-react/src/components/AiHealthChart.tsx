import { useEffect, useRef, useState } from "react";
import type { DailyAiStat } from "../lib/api";

interface Props {
  daily: DailyAiStat[];
  emptyLabel: string;
  requestsLegend: string;
  latencyLegend: string;
}

const MARGIN_LEFT = 32;
const MARGIN_RIGHT = 46;
const MARGIN_TOP = 14;
// Room for the date labels, drawn INSIDE the svg at the exact same x as each data point (see
// below) -- they used to live in a separate HTML flex row below the svg, with each label given
// an equal-width flex cell. That divides the row into n equal slices, but the chart itself
// places point i at MARGIN_LEFT + i * (plotW / (n - 1)) -- n-1 divisions, not n -- so the two
// used genuinely different math and drifted further apart moving right across the chart (LIVE-
// REPORTED: dates not lining up with their own points). Putting the labels in the same
// coordinate system as the points is what actually guarantees they match.
const MARGIN_BOTTOM = 22;

function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/** Admin dashboard's AI health chart: daily Ask Sarthi request volume as a filled area + line
 * (left axis), daily average latency as a dashed line (right axis, independent scale) -- a bar
 * chart was tried and rejected for the volume metric, since several real days have very low
 * counts (2, 5, 11 requests) that draw as near-invisible slivers as bars, reading as "missing
 * data" rather than "genuinely low traffic". Sizes itself to its container's real pixel
 * dimensions via ResizeObserver (not a fixed aspect ratio) so it fills whatever height the
 * location panel next to it stretches this row to, with no dead gap underneath. */
export default function AiHealthChart({ daily, emptyLabel, requestsLegend, latencyLegend }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  // The ref-bearing wrap div is ALWAYS rendered below (never behind an early `if (empty) return`
  // branch) -- this effect only runs once, on mount, and an earlier version returned a ref-less
  // "empty" placeholder on that very first render (since `daily` starts as `[]` before the fetch
  // resolves). That left `wrapRef.current` null when this effect ran, so it bailed out and never
  // attached the ResizeObserver at all -- by the time `daily` populated and the real chart
  // markup (with the ref) rendered, the effect had already run its once-only pass and wouldn't
  // run again, so `size` stayed stuck at {0,0} forever (confirmed live: viewBox rendered as
  // "0 0 1 1"). Keeping the wrap div's identity stable across the empty -> populated transition
  // is what makes the observer actually see it.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: el.clientHeight });
    });
    observer.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => observer.disconnect();
  }, []);

  const hasData = daily.length > 0;
  const w = Math.max(size.w, 1);
  const h = Math.max(size.h, 1);
  const n = daily.length;
  const plotW = w - MARGIN_LEFT - MARGIN_RIGHT;
  const stepX = n > 1 ? plotW / (n - 1) : 0;
  const baseline = h - MARGIN_BOTTOM;
  const plotH = h - MARGIN_TOP - MARGIN_BOTTOM;
  const maxVol = hasData ? Math.max(...daily.map((d) => d.request_count), 1) : 1;
  const maxLat = hasData ? Math.max(...daily.map((d) => d.average_latency_ms), 1) : 1;
  const px = (i: number) => MARGIN_LEFT + i * stepX;

  const volPoints = daily.map((d, i) => [px(i), baseline - (d.request_count / maxVol) * plotH] as const);
  const latPoints = daily.map((d, i) => [px(i), baseline - (d.average_latency_ms / maxLat) * plotH] as const);
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  const volLinePoints = volPoints.map((p) => p.join(",")).join(" ");
  const volAreaPath = hasData
    ? `M${volPoints[0][0]},${baseline} ` + volPoints.map((p) => `L${p[0]},${p[1]}`).join(" ") + ` L${volPoints[n - 1][0]},${baseline} Z`
    : "";
  const latLinePoints = latPoints.map((p) => p.join(",")).join(" ");

  return (
    <div className="admin-loc-ai-body">
      <div className="ai-chart-box">
        <div className="ai-chart-wrap" ref={wrapRef}>
          {hasData ? (
            <svg viewBox={`0 0 ${w} ${h}`}>
              <defs>
                <linearGradient id="adminAiVolFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--accent-fg)" stopOpacity="0.38" />
                  <stop offset="100%" stopColor="var(--accent-fg)" stopOpacity="0" />
                </linearGradient>
              </defs>
              <g>
                {ticks.map((t) => {
                  const y = baseline - t * plotH;
                  return <line key={t} className="ai-chart-gridline" x1={MARGIN_LEFT} y1={y} x2={w - MARGIN_RIGHT} y2={y} />;
                })}
              </g>
              <g>
                {ticks.map((t) => {
                  const y = baseline - t * plotH;
                  return (
                    <text key={t} className="ai-chart-ylabel" x={MARGIN_LEFT - 8} y={y + 3} textAnchor="end">
                      {Math.round(t * maxVol)}
                    </text>
                  );
                })}
              </g>
              <g>
                {ticks.map((t) => {
                  const y = baseline - t * plotH;
                  return (
                    <text key={t} className="ai-chart-ylabel" x={w - MARGIN_RIGHT + 8} y={y + 3} textAnchor="start" fill="var(--status-open)">
                      {formatMs(t * maxLat)}
                    </text>
                  );
                })}
              </g>
              <path d={volAreaPath} fill="url(#adminAiVolFill)" stroke="none" />
              <polyline points={volLinePoints} fill="none" stroke="var(--accent-fg)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
              <g>
                {daily.map((d, i) => (
                  <circle key={d.date} cx={volPoints[i][0]} cy={volPoints[i][1]} r="4" fill="var(--surface)" stroke="var(--accent-fg)" strokeWidth="2.5">
                    <title>{`${d.date}: ${d.request_count} requests`}</title>
                  </circle>
                ))}
              </g>
              <polyline
                points={latLinePoints}
                fill="none"
                stroke="var(--status-open)"
                strokeWidth="2.5"
                strokeLinejoin="round"
                strokeLinecap="round"
                strokeDasharray="5,4"
              />
              <g>
                {daily.map((d, i) => (
                  <circle key={d.date} cx={latPoints[i][0]} cy={latPoints[i][1]} r="4" fill="var(--surface)" stroke="var(--status-open)" strokeWidth="2.5">
                    <title>{`${d.date}: ${formatMs(d.average_latency_ms)} avg latency`}</title>
                  </circle>
                ))}
              </g>
              <g>
                {daily.map((d, i) => (
                  <text key={d.date} className="ai-chart-ylabel" x={volPoints[i][0]} y={h - 6} textAnchor="middle">
                    {d.date.slice(5).replace("-", "/")}
                  </text>
                ))}
              </g>
            </svg>
          ) : (
            <div className="ai-chart-empty">{emptyLabel}</div>
          )}
        </div>
      </div>
      {hasData && (
        <div className="ai-chart-legend">
          <span>
            <i style={{ background: "var(--accent-fg)" }} />
            {requestsLegend}
          </span>
          <span>
            <i
              style={{
                background: "var(--status-open)",
                backgroundImage: "linear-gradient(90deg,var(--status-open) 60%,transparent 60%)",
                backgroundSize: "6px 3px",
              }}
            />
            {latencyLegend}
          </span>
        </div>
      )}
    </div>
  );
}
