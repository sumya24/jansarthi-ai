import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import TopBar from "../components/TopBar";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, type AppNotification } from "../lib/api";
import { formatDate, formatDateTime, type LangCode } from "../lib/i18n";
import { localizeWardText } from "../lib/locationNames";
import "../styles/dashboard.css";

type MonthBucket = { label: string; count: number; year: number; month: number };

function lastSixMonthBuckets(lang: LangCode): MonthBucket[] {
  const now = new Date();
  const buckets: MonthBucket[] = [];
  for (let i = 5; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    buckets.push({ label: formatDate(d, lang, { month: "short" }), count: 0, year: d.getFullYear(), month: d.getMonth() });
  }
  return buckets;
}

function greetingKey(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "home.greeting.morning";
  if (hour < 17) return "home.greeting.afternoon";
  return "home.greeting.evening";
}

/** Citizen Home — dashboard landing screen. Reuses the app's own existing components/styles
 * throughout (surface-card, stat-card, TopBar's own notification bell); the only new visual
 * pieces are the bar chart and the two notification-backed panels, both added to dashboard.css
 * alongside the equivalent widgets already used by MyArea/CitizenDashboard/WorkerDashboard. */
export default function CitizenHome() {
  const { token, user } = useAuth();
  const { lang } = useUiLang();
  const navigate = useNavigate();

  const [totalCount, setTotalCount] = useState(0);
  const [resolvedCount, setResolvedCount] = useState(0);
  const [wardTotal, setWardTotal] = useState<number | null>(null);
  const [monthly, setMonthly] = useState<MonthBucket[]>(() => lastSixMonthBuckets(lang));
  const [notifications, setNotifications] = useState<AppNotification[]>([]);

  const ward = user?.ward ?? null;
  const openCount = totalCount - resolvedCount;

  useEffect(() => {
    if (!token) return;
    api
      .listComplaints(token, { page: 1, pageSize: 1 })
      .then((data) => setTotalCount(data.total))
      .catch(() => {});
    api
      .listComplaints(token, { status: "resolved", page: 1, pageSize: 1 })
      .then((data) => setResolvedCount(data.total))
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    if (!token || !ward) return;
    api
      .getAreaSummary(token, { page: 1, pageSize: 1 })
      .then((summary) => setWardTotal(summary.total))
      .catch(() => {});
  }, [token, ward]);

  // The chart is the citizen's OWN complaint history, not the ward's -- Home is a personal
  // dashboard (greeting, personal stat cards), and "Ward total" already covers ward-wide context
  // as its own single number, so this stays about "you," not "your neighborhood," and works the
  // same whether or not a ward is even set.
  useEffect(() => {
    if (!token) return;
    api
      .listComplaints(token, { page: 1, pageSize: 100 })
      .then((data) => {
        const buckets = lastSixMonthBuckets(lang);
        for (const c of data.items) {
          const d = new Date(c.created_at);
          const bucket = buckets.find((b) => b.year === d.getFullYear() && b.month === d.getMonth());
          if (bucket) bucket.count += 1;
        }
        setMonthly(buckets);
      })
      .catch(() => {});
  }, [token, lang]);

  useEffect(() => {
    if (!token) return;
    api
      .listNotifications(token)
      .then((data) => setNotifications([...data.notifications].sort((a, b) => b.created_at.localeCompare(a.created_at))))
      .catch(() => {});
  }, [token]);

  async function handleNotificationSelect(n: AppNotification) {
    if (!n.read_at && token) {
      try {
        await api.markNotificationRead(token, n.id);
        setNotifications((prev) => prev.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)));
      } catch {
        /* best-effort -- navigation still proceeds even if marking read failed */
      }
    }
    if (n.complaint_id) navigate(`/citizen/complaints/${n.complaint_id}`);
  }

  const unread = notifications.filter((n) => !n.read_at).slice(0, 4);
  const recent = notifications.slice(0, 5);
  const maxMonthly = Math.max(1, ...monthly.map((b) => b.count));
  const todayLabel = formatDate(new Date(), lang, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const firstName = user?.full_name?.split(" ")[0] ?? "";

  return (
    <div>
      <TopBar />
      <div className="page" id="main-content">
        <div style={{ marginBottom: 24 }}>
          <h1 className="page-title display">{t(lang, greetingKey())}, {firstName}</h1>
          <p className="page-sub">{todayLabel}{ward ? ` · ${localizeWardText(ward, lang)}` : ""}</p>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 20 }}>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "home.statTotal")}</div>
            <div className="display stat-value">{totalCount}</div>
          </div>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "citizen.open")}</div>
            <div className="display stat-value" style={{ color: "var(--status-open)" }}>{openCount}</div>
          </div>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "citizen.resolved")}</div>
            <div className="display stat-value" style={{ color: "var(--status-resolved)" }}>{resolvedCount}</div>
          </div>
          {ward && (
            <div className="surface-card hoverable stat-card">
              <div className="stat-label">{t(lang, "home.statWard")}</div>
              <div className="display stat-value">{wardTotal ?? "–"}</div>
            </div>
          )}
        </div>

        <div className="home-panels" style={{ marginBottom: 16 }}>
          <div className="surface-card" style={{ padding: "16px 18px" }}>
            <div className="home-panel-title">
              <span>{t(lang, "citizen.yourComplaints")}</span>
              <span className="sub">{t(lang, "home.chartSubtitle")}</span>
            </div>
            <div className="home-bars">
              {monthly.map((b, i) => (
                <div key={i} className="bar" style={{ height: `${(b.count / maxMonthly) * 100}%` }} title={`${b.label}: ${b.count}`} />
              ))}
            </div>
            <div className="home-bars-labels">
              {monthly.map((b, i) => <span key={i}>{b.label}</span>)}
            </div>
          </div>

          <div className="surface-card" style={{ padding: "16px 18px" }}>
            <div className="home-panel-title"><span>{t(lang, "home.attentionTitle")}</span></div>
            {unread.length === 0 && <p style={{ fontSize: 13, color: "var(--ink-2)", margin: 0 }}>{t(lang, "home.attentionEmpty")}</p>}
            {unread.map((n) => (
              <button key={n.id} type="button" className="home-attn-row" onClick={() => handleNotificationSelect(n)}>
                {/* LIVE-REPORTED: the unread dot used to sit at the far right of the row (a lone
                    `space-between` flex child) -- easy to miss since a citizen's eye lands on the
                    title/message text first, at the LEFT edge, same reading direction as
                    everywhere else in the app. Moved to lead the row instead, matching the
                    notification bell dropdown's own unread-dot placement (NotificationBell.tsx). */}
                <span className="home-attn-dot" />
                <div>
                  <div className="home-attn-title">{n.title}</div>
                  <div className="home-attn-msg">{n.message}</div>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="surface-card" style={{ padding: "16px 18px" }}>
          <div className="home-panel-title"><span>{t(lang, "home.activityTitle")}</span></div>
          {recent.length === 0 && <p style={{ fontSize: 13, color: "var(--ink-2)", margin: 0 }}>{t(lang, "notifications.empty")}</p>}
          {recent.map((n) => (
            <button key={n.id} type="button" className="home-feed-row" onClick={() => handleNotificationSelect(n)}>
              <span className={`home-feed-dot${n.read_at ? " read" : " unread"}`} />
              <div>
                <div className="home-feed-text">{n.title}</div>
                <div className="home-attn-msg">{n.message}</div>
                <div className="home-feed-time">{formatDateTime(n.created_at, lang)}</div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
