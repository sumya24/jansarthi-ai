import { useEffect, useRef, useState } from "react";
import { t, type LangCode } from "../lib/i18n";

/** Same hand-drawn stroke language used across the app's other local icons (viewBox 0 0 24 24,
 * ~1.6px stroke, currentColor, rounded caps/joins). */
function CalendarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <rect x="3.5" y="5" width="17" height="15" rx="2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M3.5 9.5h17" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 3v4M16 3v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

interface Props {
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder: string;
  dateFrom: string;
  dateTo: string;
  onDateFromChange: (value: string) => void;
  onDateToChange: (value: string) => void;
  lang: LangCode;
  width?: number;
  /** Called whenever a keystroke/date pick happens, in addition to the individual on*Change
   * callbacks -- most callers use this to clear a bulk-selection Set, same as every existing
   * search box's own onChange already did before this component existed. */
  onAnyChange?: () => void;
}

/** A text search box with a calendar-icon toggle inside it for an optional date range -- the
 * icon opens a small popover with native "From"/"To" date inputs (LIVE-REPORTED: a custom-built
 * calendar grid was tried here first, but every version of it (arrows-only, a year <select>, a
 * year number input) was rejected in favor of just the browser's own native date picker, so this
 * deliberately stays native rather than reinventing it again). One shared component so every
 * list's search box gets the same date-filter affordance instead of five near-identical inline
 * copies. */
export default function SearchWithDateFilter({
  searchValue,
  onSearchChange,
  searchPlaceholder,
  dateFrom,
  dateTo,
  onDateFromChange,
  onDateToChange,
  lang,
  width = 280,
  onAnyChange,
}: Props) {
  const [showDatePopover, setShowDatePopover] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showDatePopover) return;
    function handlePointerDown(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setShowDatePopover(false);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setShowDatePopover(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [showDatePopover]);

  const hasDateFilter = Boolean(dateFrom || dateTo);
  const fromId = `date-from-${searchPlaceholder.replace(/\s+/g, "-")}`;
  const toId = `date-to-${searchPlaceholder.replace(/\s+/g, "-")}`;

  return (
    <div ref={popoverRef} style={{ position: "relative", width, maxWidth: "100%", flexShrink: 0 }}>
      <div className="field" style={{ margin: 0 }}>
        <input
          type="text"
          aria-label={searchPlaceholder}
          placeholder={searchPlaceholder}
          value={searchValue}
          onChange={(e) => {
            onSearchChange(e.target.value);
            onAnyChange?.();
          }}
          style={{ paddingRight: 34, textOverflow: "ellipsis" }}
        />
        <button
          type="button"
          className="icon-btn"
          aria-label={t(lang, "admin.filterByDate")}
          title={t(lang, "admin.filterByDate")}
          onClick={() => setShowDatePopover((v) => !v)}
          style={{
            position: "absolute",
            right: 5,
            top: "50%",
            transform: "translateY(-50%)",
            width: 26,
            height: 26,
            background: hasDateFilter ? "var(--accent)" : "transparent",
            color: hasDateFilter ? "var(--accent-ink)" : "var(--ink-3)",
          }}
        >
          <CalendarIcon />
        </button>
      </div>

      {showDatePopover && (
        <div
          className="surface-card"
          style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 20, padding: 14, width: 260, boxShadow: "var(--shadow-lg)" }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <label htmlFor={fromId} style={{ display: "block", fontSize: 11, fontWeight: 700, color: "var(--ink-2)", marginBottom: 4 }}>
                {t(lang, "admin.dateFrom")}
              </label>
              <input
                id={fromId}
                type="date"
                value={dateFrom}
                max={dateTo || undefined}
                style={{ width: "100%" }}
                onChange={(e) => {
                  onDateFromChange(e.target.value);
                  onAnyChange?.();
                }}
              />
            </div>
            <div>
              <label htmlFor={toId} style={{ display: "block", fontSize: 11, fontWeight: 700, color: "var(--ink-2)", marginBottom: 4 }}>
                {t(lang, "admin.dateTo")}
              </label>
              <input
                id={toId}
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                style={{ width: "100%" }}
                onChange={(e) => {
                  onDateToChange(e.target.value);
                  onAnyChange?.();
                }}
              />
            </div>
            {hasDateFilter && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  onDateFromChange("");
                  onDateToChange("");
                }}
              >
                {t(lang, "admin.clearDates")}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
