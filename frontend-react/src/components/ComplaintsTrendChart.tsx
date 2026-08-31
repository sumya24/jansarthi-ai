import { useEffect, useRef, useState } from "react";
import type { DailyComplaintTrend } from "../lib/api";

type SeriesKey = "opened" | "resolved";

interface Props {
  daily: DailyComplaintTrend[];
  emptyLabel: string;
  legends: Record<SeriesKey, string>;
}

const MARGIN_LEFT = 28;
// Wide enough for the last x-axis date label's full width to fit past the last plotted point --
// that label is centered ON the point (textAnchor="middle"), so it extends roughly half its own
// width beyond the plot area on the right; too small a margin here clips it (LIVE-REPORTED: "08/31"
// rendering as "08/3").
const MARGIN_RIGHT = 20;
const MARGIN_TOP = 14;
const MARGIN_BOTTOM = 22;

// Same 2 colors already used elsewhere for these exact concepts (status badges, filter chips) --
// not a fresh palette invented for this chart. Accepted/rejected activity is real too, but shown
// as all-time totals in the Resolution Rate card instead of extra lines here -- this chart stays
// a plain "opened vs. resolved" read, not a 4-line workflow diagram.
const SERIES: { key: SeriesKey; color: string }[] = [
  { key: "opened", color: "var(--status-open)" },
  { key: "resolved", color: "var(--status-resolved)" },
];

/** Worker dashboard's "Opened vs. resolved" chart -- real daily counts, both series sharing one
 * y-axis (unlike AdminDashboard's AiHealthChart, which needs two independent scales for requests
 * vs. latency: opened/resolved are the same unit, a shared axis is the honest comparison). Same
 * ResizeObserver-based sizing, gridline/axis-label/marker-with-tooltip styling as AiHealthChart,
 * reused (not duplicated) via the shared `.ai-chart-*` classes -- see that component for the
 * fuller rationale on each piece.
 *
 * Both series are genuinely real -- "opened" from each complaint's `created_at`, "resolved" from
 * `ComplaintStatusHistory.to_status == "resolved"` (see backend/routes/complaints.py's
 * complaints_trend docstring), never Complaint.status. */
export default function ComplaintsTrendChart({ daily, emptyLabel, legends }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

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
  // `?? 0` on every read of d[s.key] below -- defends against a day row that's missing a series
  // entirely (LIVE-REPORTED: a page loaded against an older backend response shape that hadn't
  // added "accepted"/"rejected" yet rendered every point as NaN, since `undefined / max` is NaN
  // and Math.max with any NaN argument returns NaN too, silently breaking the WHOLE chart from
  // one stale field rather than just that one series).
  const value = (d: DailyComplaintTrend, key: SeriesKey) => d[key] ?? 0;
  const max = hasData ? Math.max(...daily.flatMap((d) => SERIES.map((s) => value(d, s.key))), 1) : 1;
  const px = (i: number) => MARGIN_LEFT + i * stepX;
  const py = (v: number) => baseline - (v / max) * plotH;
  const ticks = [0, 0.5, 1];

  const seriesPoints = SERIES.map((s) => ({
    ...s,
    points: daily.map((d, i) => [px(i), py(value(d, s.key))] as const),
    total: daily.reduce((sum, d) => sum + value(d, s.key), 0),
  }));

  return (
    <div className="admin-loc-ai-body">
      <div className="ai-chart-box">
        <div className="ai-chart-wrap" ref={wrapRef}>
          {hasData ? (
            <svg viewBox={`0 0 ${w} ${h}`}>
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
                      {Math.round(t * max)}
                    </text>
                  );
                })}
              </g>
              {seriesPoints.map((s) => (
                <g key={s.key}>
                  <polyline
                    points={s.points.map((p) => p.join(",")).join(" ")}
                    fill="none" stroke={s.color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round"
                  />
                  {daily.map((d, i) => (
                    <circle key={d.date} cx={s.points[i][0]} cy={s.points[i][1]} r="3.5" fill="var(--surface)" stroke={s.color} strokeWidth="2.2">
                      <title>{`${d.date}: ${value(d, s.key)} ${legends[s.key].toLowerCase()}`}</title>
                    </circle>
                  ))}
                </g>
              ))}
              <g>
                {daily.map((d, i) => (
                  <text key={d.date} className="ai-chart-ylabel" x={px(i)} y={h - 6} textAnchor="middle">
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
          {seriesPoints.map((s) => (
            <span key={s.key}>
              <i style={{ background: s.color }} />
              {legends[s.key]} <b className="mono">{s.total}</b>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
