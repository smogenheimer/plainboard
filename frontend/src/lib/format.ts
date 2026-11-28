export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
}

export function formatCost(usd: number): string {
  if (!usd) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatBytes(n: number): string {
  if (n >= 1024 ** 4) return `${(n / 1024 ** 4).toFixed(1)} TB`;
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  if (!Number.isFinite(seconds)) return "";
  const abs = Math.abs(seconds);
  if (abs < 45) return rtf.format(Math.round(seconds), "second");
  if (abs < 2700) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 64800) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}

const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
const calendarDays = (date: Date) => Math.round((dayStart(date) - dayStart(new Date())) / 86_400_000);

/** The exact local time of a point near now, as short as it stays clear: the time today, "tomorrow 04:00",
 *  else weekday, date and time ("Mo., 30.11. 04:00" in German, "Mon, 11/30 4:00 AM" in US English). */
export function exactTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString(undefined, { timeStyle: "short" });
  const days = calendarDays(date);
  if (days === 0) return time;
  if (days === 1) return `${rtf.format(1, "day")} ${time}`;
  return `${date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "numeric" })} ${time}`;
}

/** A point ahead (a limit's reset, a forecast running out, a pause ending, #184): relative and exact in the
 *  user's locale, e.g. "in 3 hours · 14:30", "tomorrow · 14:30", "in 3 days · Mon, 11/30 4:00 AM". Past
 *  18 hours the relative part counts calendar days, so "tomorrow" is never the day after. */
export function whenAhead(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = (date.getTime() - Date.now()) / 1000;
  const days = calendarDays(date);
  if (Math.abs(seconds) < 64800 || days === 0) {
    const relative = Math.abs(seconds) < 64800 ? timeAgo(iso) : rtf.format(Math.round(seconds / 3600), "hour");
    return `${relative} · ${exactTime(iso)}`;
  }
  const exact = days === 1 ? date.toLocaleTimeString(undefined, { timeStyle: "short" }) : exactTime(iso);
  return `${rtf.format(days, "day")} · ${exact}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** For chat messages: the time, with the date unless it is today. */
export function chatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = date.toDateString() === new Date().toDateString();
  return today ? date.toLocaleTimeString(undefined, { timeStyle: "short" }) : date.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

/** ISO (UTC) -> value for <input type="datetime-local"> in local time. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
