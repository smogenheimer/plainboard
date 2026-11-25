import clsx from "clsx";
import type { TFunction } from "i18next";
import { TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAccounts, useLimits } from "../../api/queries";
import type { AccountLimits, LimitForecast, LimitWindow } from "../../api/types";
import { timeAgo } from "../../lib/format";
import { useDismiss, useInView } from "../../lib/hooks";
import { useShell } from "../layout/shell-context";
import { useUsableAccounts } from "../projects/ProjectAccounts";
import { StatusMark, useApiUse } from "../settings/AccountStatus";

const tone = (used: number) => (used >= 0.9 ? "red" : used >= 0.75 ? "amber" : "indigo");
const stroke = { red: "text-you", amber: "text-fg", indigo: "text-fg-2" };
const bar = { red: "bg-you", amber: "bg-fg", indigo: "bg-fg-2" };
const percent = (used: number) => Math.round(used * 100);
const fromUnix = (seconds: number) => new Date(seconds * 1000).toISOString();
const STALE_AFTER = 45 * 60; // seconds

/** The accounts whose limits matter here: all of them, or the ones the open project may use. */
function useProjectLimits() {
  const { data } = useLimits();
  const allowed = useUsableAccounts(useShell().project.id);
  return data && { accounts: allowed ? data.accounts.filter((a) => allowed.includes(a.account_id)) : data.accounts };
}

/** Plan limits (5-hour, weekly) as one small ring per account, filled to the fullest window; click
 * for the windows, resets and forecast. Sits under every composer, so usage is always in view. An API
 * access (no plan limits) or a subscription not read yet gets an empty, dashed ring that says so. */
export function LimitsRing() {
  const data = useProjectLimits();
  if (!data) return null;
  if (!data.accounts.length) return <Ring available={false} />;
  return (
    <>
      {data.accounts.map((account) => (
        <Ring key={account.account_id} account={account} />
      ))}
    </>
  );
}

/** Every account's limits in the chat's context column, one line each: name, the 5-hour window and
 * a short bar of the fullest weekly one. An account that runs out before its reset is vermilion with a
 * warning sign. The resets and forecasts are a click away on the rings under the composer. */
export function LimitsList() {
  const { t } = useTranslation();
  const data = useProjectLimits();
  if (!data) return null;
  if (!data.accounts.length) return <p className="px-2.5 py-2 text-fg-3">{t("chat.limits.noPlan")}</p>;
  return (
    <>
      {data.accounts.map((account) => (
        <LimitLine key={account.account_id} account={account} />
      ))}
    </>
  );
}

/** What stands in place of the values when there are none: an API access has no plan limits, a
 * subscription's are not read yet. */
const noValues = (t: TFunction, account: AccountLimits, short = false) =>
  account.available ? t("chat.limits.pending") : t(short ? "chat.limits.api" : "chat.limits.apiLine");

function LimitLine({ account }: { account: AccountLimits }) {
  const { t } = useTranslation();
  if (!account.windows.length)
    return (
      <div className="grid h-9 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-b border-line px-2.5" title={t(account.available ? "chat.limits.unknown" : "chat.limits.apiExplain")}>
        <span className="truncate font-medium">{account.label}</span>
        <span className="text-fg-3">{noValues(t, account)}</span>
      </div>
    );
  const five = account.windows.find((w) => w.name.endsWith("five_hour"));
  const weeks = account.windows.filter((w) => w !== five);
  const week = weeks.length ? weeks.reduce((a, b) => (b.utilization > a.utilization ? b : a)) : undefined;
  const forecast = week && account.forecasts.find((f) => f.name === week.name);
  const risk = account.forecasts.some((f) => f.status === "over") || account.windows.some((w) => w.utilization >= 1);
  const name = (w: LimitWindow) => t(`chat.limits.${w.name}`, { defaultValue: w.name });
  return (
    <div className="grid h-9 grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-3 border-b border-line px-2.5" title={forecast ? forecastHeadline(t, forecast) : undefined}>
      <span className={clsx("flex min-w-0 items-center gap-1.5", risk && "text-you-fg")}>
        <span className="truncate font-medium">{account.label}</span>
        {risk && <TriangleAlert className="size-3.5 shrink-0" aria-label={t("chat.limits.slowDown")} />}
      </span>
      <span className="text-fg-3" title={five && name(five)}>
        {five && t("chat.limits.fiveHourShort", { percent: percent(five.utilization) })}
      </span>
      <span className="flex items-center gap-1.5" title={week && name(week)}>
        {week && (
          <>
            <span className="block h-1 w-12 bg-line">
              <span className={clsx("block h-full", risk ? "bg-you" : bar[tone(week.utilization)])} style={{ width: `${Math.min(percent(week.utilization), 100)}%` }} />
            </span>
            <span className={clsx("w-9 text-right", risk && "font-medium text-you-fg")}>{percent(week.utilization)}%</span>
          </>
        )}
      </span>
    </div>
  );
}

function Ring({ account, available = true }: { account?: AccountLimits; available?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const [popRef, inView] = useInView<HTMLDivElement>(open);
  useDismiss(ref, open, () => setOpen(false));
  const use = useApiUse(useAccounts().data?.find((a) => a.id === account?.account_id && !account.available)); // #140: when an API access runs

  const windows = account?.windows ?? [];
  const forecasts = account?.forecasts ?? [];
  const highest = Math.max(0, ...windows.map((w) => w.utilization));
  const slowDown = forecasts.some((f) => f.status === "over");
  const ringTone = tone(highest) === "indigo" && slowDown ? "amber" : tone(highest);
  const empty = !!account && !windows.length;
  const ringLabel = empty ? noValues(t, account) : t("chat.limits.ring", { percent: percent(highest) });
  // The limits are read every 15 minutes. A value much older than that could not be read: say so
  // instead of showing it like a current one.
  const stale = !!account?.updated_at && Date.now() / 1000 - account.updated_at > STALE_AFTER;
  const label =
    (account ? `${account.label}: ${ringLabel}` : ringLabel) +
    (slowDown ? ` · ${t("chat.limits.slowDown")}` : "") +
    (stale ? ` · ${t("chat.limits.updated", { when: timeAgo(fromUnix(account.updated_at!)) })}` : "");

  return (
    <div ref={ref} className="relative shrink-0">
      <button type="button" onClick={() => setOpen(!open)} aria-label={label} aria-expanded={open} title={label} className="flex h-6 items-center gap-1.5 rounded-sm px-1 text-fg-3 hover:bg-sunk hover:text-fg touch:h-9">
        <svg viewBox="0 0 20 20" className={clsx("size-4 shrink-0 -rotate-90", stale && "opacity-40")} aria-hidden>
          <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth={empty ? 2 : 3} strokeDasharray={empty ? "3 2.6" : undefined} className="text-line-2" />
          {!empty && <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth="3" pathLength={100} strokeDasharray={`${Math.min(percent(highest), 100)} 100`} className={stroke[ringTone]} />}
        </svg>
        {account && <span className="max-w-24 truncate">{account.label}</span>}
        {empty && <span className="text-[0.6875rem] text-fg-3">{noValues(t, account, true)}</span>}
      </button>
      {open && (
        <div ref={popRef} style={inView} data-part="menu" className="absolute right-0 bottom-full z-20 mb-2 w-64 max-w-[calc(100vw-1rem)] space-y-3 rounded-sm bg-bg p-3 shadow-lg ring-1 ring-line-2">
          {account && <p className="font-semibold">{account.label}</p>}
          {windows.map((window) => (
            <LimitRow key={window.name} window={window} forecast={forecasts.find((f) => f.name === window.name)} />
          ))}
          {!windows.length && <p className="text-fg-3">{t(!account ? (available ? "chat.limits.unknown" : "chat.limits.noPlan") : account.available ? "chat.limits.unknown" : "chat.limits.apiExplain")}</p>}
          {use && (
            <p className="flex items-start gap-1.5">
              <StatusMark tone={use.tone} className="mt-[0.4lh]" />
              <span>{use.text}</span>
            </p>
          )}
          {account?.updated_at && <p className="text-fg-3">{t("chat.limits.updated", { when: timeAgo(fromUnix(account.updated_at)) })}</p>}
        </div>
      )}
    </div>
  );
}

const forecastTone = {
  ok: "text-ok",
  tight: "text-fg",
  over: "text-you-fg",
};

const forecastHeadline = (t: TFunction, forecast: LimitForecast) =>
  forecast.status === "over"
    ? forecast.runs_out_at
      ? t("chat.limits.forecast.over", { percent: percent(forecast.projected), when: timeAgo(fromUnix(forecast.runs_out_at)) })
      : t("chat.limits.forecast.reached")
    : t(`chat.limits.forecast.${forecast.status}`, { percent: percent(forecast.projected) });

function Forecast({ forecast }: { forecast: LimitForecast }) {
  const { t } = useTranslation();
  const headline = forecastHeadline(t, forecast);
  return (
    <div className="mt-1.5 space-y-0.5">
      <p className={clsx("font-medium", forecastTone[forecast.status])}>{headline}</p>
      <p className="text-fg-3">{t("chat.limits.forecast.pace", { daily: percent(forecast.daily), budget: percent(forecast.budget) })}</p>
      <p className="text-fg-3">{forecast.basis_days ? t("chat.limits.forecast.basisDays", { count: forecast.basis_days }) : t("chat.limits.forecast.basisWeek")}</p>
    </div>
  );
}

function LimitRow({ window, forecast }: { window: LimitWindow; forecast?: LimitForecast }) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="flex justify-between gap-2 font-medium">
        <span>{t(`chat.limits.${window.name}`, { defaultValue: window.name })}</span>
        <span>{percent(window.utilization)}%</span>
      </div>
      <div className="mt-1 h-1.5 bg-line">
        <div className={clsx("h-full", bar[tone(window.utilization)])} style={{ width: `${Math.min(percent(window.utilization), 100)}%` }} />
      </div>
      {window.resets_at && <p className="mt-1 text-fg-3">{t("chat.limits.resets", { when: timeAgo(fromUnix(window.resets_at)) })}</p>}
      {forecast && <Forecast forecast={forecast} />}
    </div>
  );
}
