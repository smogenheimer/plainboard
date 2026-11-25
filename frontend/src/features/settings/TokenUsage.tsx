import clsx from "clsx";
import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useTokenUsage } from "../../api/queries";
import type { TokenProject } from "../../api/types";
import { ErrorText, Segmented, Spinner } from "../../components/ui";
import { useStored } from "../../lib/hooks";

const RANGES = [7, 30];
/** Projects with a colour of their own; the rest are one grey series. */
const COLORED = 9;
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const count = (value: number) => compact.format(value);
const money = (value: number) => `$${value.toFixed(2)}`;
const shortDate = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
const percent = (part: number, whole: number) => (whole ? `${Math.round((part / whole) * 1000) / 10} %` : "–");

type Metric = "cost" | "tokens";

interface Series {
  key: string;
  label: string;
  color: string;
  values: number[];
  total: number;
}

const swatch = (color: string): CSSProperties => ({ background: color });

/** Which projects used how many tokens and what they cost: stacked per day, and ranked with their share. */
export function TokenUsage() {
  const { t } = useTranslation();
  const [days, setDays] = useStored<number>("plainboard.tokenDays", 30);
  const [metric, setMetric] = useStored<Metric>("plainboard.tokenMetric", "cost");
  const usage = useTokenUsage(days);
  const data = usage.data;
  const format = metric === "cost" ? money : count;

  const controls = (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented value={days} onChange={setDays} options={RANGES.map((d) => ({ value: d, label: t("stats.lastDays", { count: d }) }))} aria-label={t("tokens.range")} />
      <Segmented
        value={metric}
        onChange={setMetric}
        options={[
          { value: "cost", label: t("stats.cost") },
          { value: "tokens", label: t("stats.tokens") },
        ]}
        aria-label={t("tokens.metric")}
      />
    </div>
  );
  if (!data) return <div className="space-y-4">{controls}{usage.error ? <ErrorText error={usage.error} /> : <Spinner />}</div>;

  const pick = (p: TokenProject) => (metric === "cost" ? p.costs : p.tokens);
  const totalOf = (p: TokenProject) => (metric === "cost" ? p.cost_usd : p.input_tokens + p.output_tokens);
  const ranked = [...data.projects].sort((a, b) => totalOf(b) - totalOf(a));
  const colorOf = new Map(ranked.slice(0, COLORED).map((p, i) => [p.id, `var(--chart-${i + 1})`]));
  const rest = ranked.slice(COLORED);
  const series: Series[] = ranked.slice(0, COLORED).map((p) => ({ key: String(p.id), label: p.name, color: colorOf.get(p.id)!, values: pick(p), total: totalOf(p) }));
  if (rest.length)
    series.push({
      key: "rest",
      label: t("tokens.rest", { count: rest.length }),
      color: "var(--fg-3)",
      values: data.days.map((_, i) => rest.reduce((sum, p) => sum + (pick(p)[i] ?? 0), 0)),
      total: rest.reduce((sum, p) => sum + totalOf(p), 0),
    });
  const grand = series.reduce((sum, s) => sum + s.total, 0);
  const totalCost = data.projects.reduce((sum, p) => sum + p.cost_usd, 0);
  const totalTokens = data.projects.reduce((sum, p) => sum + p.input_tokens + p.output_tokens, 0);

  return (
    <div className="space-y-6">
      {controls}
      <p className="text-fg-3">{t("tokens.hint")}</p>
      {data.projects.length === 0 ? (
        <p className="text-fg-3">{t("tokens.none")}</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-x-6 rule-t">
            <div className="border-b border-line py-2.5">
              <div className="text-fg-3">{t("stats.cost")}</div>
              <div className="font-extrabold">{money(totalCost)}</div>
            </div>
            <div className="border-b border-line py-2.5">
              <div className="text-fg-3">{t("stats.tokens")}</div>
              <div className="font-extrabold">{count(totalTokens)}</div>
            </div>
          </div>
          <StackedDays title={metric === "cost" ? t("tokens.costPerDay") : t("tokens.tokensPerDay")} days={data.days} series={series} format={format} />
          <section>
            <h3 className="caps rule-b pb-1.5">{t("tokens.ranking")}</h3>
            <ol>
              {ranked.map((p) => (
                <li key={p.id} className="border-b border-line py-2">
                  <div className="flex items-baseline justify-between gap-x-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="size-[7px] shrink-0 rounded-[1px]" style={swatch(colorOf.get(p.id) ?? "var(--fg-3)")} />
                      <span className="truncate font-semibold">{p.name}</span>
                    </span>
                    <span className="whitespace-nowrap tabular-nums">
                      <span className="font-semibold">{format(totalOf(p))}</span> <span className="text-fg-2">· {percent(totalOf(p), grand)}</span>
                    </span>
                  </div>
                  <div className="mt-1 h-1 w-full bg-line" aria-hidden>
                    <div className="h-full" style={{ ...swatch(colorOf.get(p.id) ?? "var(--fg-3)"), width: `${grand ? (totalOf(p) / grand) * 100 : 0}%` }} />
                  </div>
                </li>
              ))}
            </ol>
          </section>
          <section>
            <h3 className="caps rule-b pb-1.5">{t("tokens.details")}</h3>
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full tabular-nums">
                <thead>
                  <tr className="border-b border-line-2 text-left text-fg-3 [&>th]:py-2 [&>th]:pr-4 [&>th]:font-normal [&>th]:whitespace-nowrap [&>th:not(:first-child)]:text-right">
                    <th>{t("tokens.project")}</th>
                    <th>{t("stats.runs")}</th>
                    <th>{t("tokens.input")}</th>
                    <th>{t("tokens.output")}</th>
                    <th>{t("tokens.cacheRead")}</th>
                    <th>{t("tokens.cacheWrite")}</th>
                    <th>{t("stats.cost")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {ranked.map((p) => (
                    <tr key={p.id} className="[&>td]:py-2 [&>td]:pr-4 [&>td]:whitespace-nowrap [&>td:not(:first-child)]:text-right">
                      <td className="max-w-48 truncate font-medium">{p.name}</td>
                      <td>{count(p.runs)}</td>
                      <td>{count(p.input_tokens)}</td>
                      <td>{count(p.output_tokens)}</td>
                      <td className="text-fg-2">{count(p.cache_read_tokens)}</td>
                      <td className="text-fg-2">{count(p.cache_creation_tokens)}</td>
                      <td>{money(p.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-fg-3">{t("tokens.detailsHint")}</p>
          </section>
        </>
      )}
    </div>
  );
}

/** One stacked bar per day, a segment per project; hovering (tapping) a day shows its figures. */
function StackedDays({ title, days, series, format }: { title: string; days: string[]; series: Series[]; format: (value: number) => string }) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState<number | null>(null);
  const totals = days.map((_, i) => series.reduce((sum, s) => sum + (s.values[i] ?? 0), 0));
  const highest = Math.max(0, ...totals);
  const place = hovered === null ? 0 : ((hovered + 0.5) / days.length) * 100;

  return (
    <figure className="relative min-w-0">
      <figcaption className="mb-4 space-y-1.5 rule-b pb-1.5">
        <span className="caps">{title}</span>
        <span className="flex flex-wrap gap-x-3 gap-y-1 text-fg-3">
          {series.map((s) => (
            <span key={s.key} className="flex min-w-0 items-center gap-1.5">
              <span className="size-[7px] shrink-0 rounded-[1px]" style={swatch(s.color)} />
              <span className="max-w-40 truncate">{s.label}</span>
            </span>
          ))}
        </span>
      </figcaption>
      <div className="relative" aria-hidden onMouseLeave={() => setHovered(null)}>
        <div className="absolute inset-x-0 top-0 border-t border-dashed border-line" />
        <span className="absolute top-0.5 left-0 z-[1] bg-bg pr-1 text-fg-3">{format(highest)}</span>
        <div className="flex h-40 items-stretch border-b border-line">
          {days.map((day, i) => (
            <div
              key={day}
              onMouseEnter={() => setHovered(i)}
              onClick={() => setHovered(i)}
              className={clsx("flex min-w-0 flex-1 flex-col-reverse items-center px-px", hovered === i && "bg-sunk")}
            >
              {series.map((s) => {
                const value = s.values[i] ?? 0;
                return value > 0 ? <div key={s.key} className="w-full max-w-5 shrink-0" style={{ ...swatch(s.color), height: `${highest ? (value / highest) * 100 : 0}%`, minHeight: 1 }} /> : null;
              })}
            </div>
          ))}
        </div>
        {hovered !== null && (
          <div className="pointer-events-none absolute top-1 z-10 rounded-sm bg-bg px-2.5 py-1.5 whitespace-nowrap shadow-lg ring-1 ring-line-2" style={{ left: `${place}%`, transform: `translateX(-${place}%)` }}>
            <div className="font-medium">
              {shortDate(days[hovered])} · {format(totals[hovered])}
            </div>
            {series
              .filter((s) => (s.values[hovered] ?? 0) > 0)
              .map((s) => (
                <div key={s.key} className="flex items-center gap-1.5 text-fg-2">
                  <span className="size-[7px] rounded-[1px]" style={swatch(s.color)} />
                  {s.label}: <span className="font-semibold text-fg">{format(s.values[hovered])}</span>
                </div>
              ))}
          </div>
        )}
        <div className="mt-1 flex justify-between text-fg-3">
          <span>{shortDate(days[0])}</span>
          <span>{shortDate(days[days.length - 1])}</span>
        </div>
      </div>
      <table className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th>{t("stats.date")}</th>
            {series.map((s) => (
              <th key={s.key}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {days.map((day, i) => (
            <tr key={day}>
              <td>{shortDate(day)}</td>
              {series.map((s) => (
                <td key={s.key}>{format(s.values[i] ?? 0)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
