import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { qk, useUsage } from "../../api/queries";
import type { MachineUsage, RunnerUsage, Usage, UsageCleanup, UsageStorage } from "../../api/types";
import { Button, ErrorText, Segmented, Spinner } from "../../components/ui";
import { useStored } from "../../lib/hooks";
import { exactTime, formatBytes, timeAgo } from "../../lib/format";
import { CpuUsage } from "./CpuUsage";
import { TokenUsage } from "./TokenUsage";

const SHARED_SHOWN = 8;

/** One ruled row: what at the left, the figures at the right. */
function Row({ name, disk, memory, muted }: { name: string; disk?: string; memory?: string; muted?: boolean }) {
  return (
    <div className={clsx("grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-3 border-b border-line py-1.5", muted && "text-fg-2")}>
      <span className="truncate">{name}</span>
      <span className="text-right whitespace-nowrap">{disk ?? ""}</span>
      <span className="w-20 text-right whitespace-nowrap text-fg-2">{memory ?? ""}</span>
    </div>
  );
}

function Meter({ used, total, low }: { used: number; total: number; low: boolean }) {
  return (
    <div className="mt-1 h-1 w-full bg-line" aria-hidden>
      <div className={clsx("h-full", low ? "bg-you" : "bg-fg-2")} style={{ width: `${total ? Math.min(100, (used / total) * 100) : 0}%` }} />
    </div>
  );
}

/** Hours as "40 min" or "2 h 10 min". */
function hours(h: number): string {
  const minutes = Math.round(h * 60);
  return minutes < 90 ? `${minutes} min` : `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}

/** The target, the trend and its forecast. */
function StorageTarget({ storage }: { storage: UsageStorage }) {
  const { t } = useTranslation();
  const over = storage.percent != null && storage.percent >= storage.notify_percent;
  const growing = storage.rate != null && storage.rate > 0;
  const etas = growing
    ? (
        [
          [`${storage.notify_percent} %`, storage.hours_target],
          ["95 %", storage.hours_95],
          [t("cleanup.full"), storage.hours_full],
        ] as const
      )
        .filter(([, h]) => h != null && h > 0)
        .map(([what, h]) => t("cleanup.eta", { what, time: hours(h as number), at: exactTime(new Date(Date.now() + (h as number) * 3_600_000).toISOString()) }))
    : [];
  const trend = storage.rate == null ? t("cleanup.trendUnknown") : growing ? t("cleanup.growing", { rate: formatBytes(storage.rate) }) : t("cleanup.notGrowing");
  return (
    <section>
      <h3 className="caps rule-b pb-1.5">{t("cleanup.target", { percent: storage.notify_percent })}</h3>
      <p className={clsx("py-1.5", over ? "text-you-fg" : "text-fg-2")}>
        {[storage.percent != null && t("cleanup.now", { percent: Math.round(storage.percent) }), trend, ...etas].filter(Boolean).join(" · ")}
      </p>
    </section>
  );
}

/** A part that starts folded: its head with a figure at the right, the rows below once opened. */
function Fold({ title, aside, children, heading = false }: { title: ReactNode; aside?: ReactNode; children: ReactNode; heading?: boolean }) {
  return (
    <details className="group">
      <summary
        className={clsx(
          "grid cursor-pointer list-none grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-1.5 hover:text-fg [&::-webkit-details-marker]:hidden",
          heading ? "rule-b pb-1.5" : "border-b border-line py-1.5",
        )}
      >
        <ChevronRight className="size-3.5 shrink-0 text-fg-3 transition-transform group-open:rotate-90" aria-hidden />
        <span className={clsx("truncate", heading ? "caps" : "font-extrabold")}>{title}</span>
        {aside}
      </summary>
      <div className="pb-2">{children}</div>
    </details>
  );
}

/** One cleanup: when, by whom, how much, the biggest items, what it left alone. */
function Cleanup({ cleanup }: { cleanup: UsageCleanup }) {
  const { t } = useTranslation();
  const memory = cleanup.level === "memory";
  return (
    <div className="pt-2">
      <p className="py-1.5 text-fg-2">
        {memory
          ? t("cleanup.stoppedSummary", { when: timeAgo(cleanup.at), count: cleanup.items.length })
          : t(`cleanup.summary.${cleanup.level}`, { when: timeAgo(cleanup.at), freed: formatBytes(cleanup.freed), free: formatBytes(cleanup.free_before), by: cleanup.by, project: cleanup.project })}
      </p>
      {cleanup.items.map((item) => (
        <Row key={item.path} name={`${t(`cleanup.what.${item.what}`)} · ${item.path}`} disk={memory ? undefined : formatBytes(item.bytes)} muted />
      ))}
      {cleanup.kept.map((reason) => (
        <Row key={reason} name={reason} muted />
      ))}
    </div>
  );
}

/** The external runner's state while it is away: since when, and how old the numbers are. */
function Away({ runner }: { runner: RunnerUsage }) {
  const { t } = useTranslation();
  if (runner.connected) return null;
  return (
    <p className="text-you-fg">
      {runner.last_seen_at ? t("usage.runnerAway", { when: timeAgo(runner.last_seen_at) }) : t("usage.runnerNeverConnected")}{" "}
      {runner.known_at ? t("usage.runnerLastKnown", { when: timeAgo(runner.known_at) }) : t("usage.runnerNothingKnown")}
    </p>
  );
}

/** A machine at a glance: its volumes, memory and CPU, each with a meter; the CPU per project folded below. */
function Glance({ data, appData }: { data: MachineUsage; appData?: number | null }) {
  const { t } = useTranslation();
  const load = data.cpu_load[0];
  const busy = data.cpu_cores > 0 && load != null && load > data.cpu_cores;
  return (
    <section>
      <h3 className="caps rule-b pb-1.5">{t("usage.volumes")}</h3>
      {data.volumes.map((v) => (
        <div key={v.kinds.join()} className="border-b border-line py-2">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="font-semibold">{v.kinds.map((k) => t(`usage.kind.${k}`)).join(" · ")}</span>
            <span className={clsx("whitespace-nowrap", v.low ? "text-you-fg" : "text-fg-2")}>
              {t("usage.volumeFigures", { used: formatBytes(v.used), total: formatBytes(v.total), free: formatBytes(v.free) })}
            </span>
          </div>
          <Meter used={v.used} total={v.total} low={v.low} />
          {v.low && <p className="mt-1 text-you-fg">{t("usage.low")}</p>}
        </div>
      ))}
      {data.memory_total > 0 && (
        <div className="border-b border-line py-2">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="font-semibold">{t("usage.memory")}</span>
            <span className="whitespace-nowrap text-fg-2">{t("usage.memoryFigures", { used: formatBytes(data.memory_used), total: formatBytes(data.memory_total) })}</span>
          </div>
          <Meter used={data.memory_used} total={data.memory_total} low={false} />
        </div>
      )}
      {data.cpu_cores > 0 && load != null && (
        <div className="border-b border-line py-2">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="font-semibold">{t("usage.cpu")}</span>
            <span className={clsx("whitespace-nowrap", busy ? "text-you-fg" : "text-fg-2")}>{t("usage.cpuLoadFigures", { load: load.toFixed(2), cores: data.cpu_cores })}</span>
          </div>
          <Meter used={load} total={data.cpu_cores} low={busy} />
        </div>
      )}
      {appData != null && <Row name={t("usage.data")} disk={formatBytes(appData)} muted />}
      {data.cpu_cores > 0 && (
        <div className="pt-4">
          <Fold heading title={t("usage.cpuDetails")}>
            <div className="pt-2">
              <CpuUsage data={data} />
            </div>
          </Fold>
        </div>
      )}
    </section>
  );
}

/** A machine's disk and memory per project (biggest first) and agent, and what all agents share; each folded. */
function Storage({ data }: { data: MachineUsage }) {
  const { t } = useTranslation();
  const disk = (n: number | null | undefined) => (n == null ? (data.measured_at ? "–" : "") : formatBytes(n));
  const memory = (n: number) => (n ? formatBytes(n) : "");
  const shared = data.shared.slice(0, SHARED_SHOWN);
  const sharedRest = data.shared.slice(SHARED_SHOWN).reduce((sum, f) => sum + f.bytes, 0);
  const sharedTotal = data.shared.reduce((sum, f) => sum + f.bytes, 0);
  const projects = data.projects
    .map((p) => ({
      p,
      disk: p.agents.reduce((sum, a) => sum + (a.disk ?? 0), 0) + p.uploads + p.other_disk,
      memory: p.agents.reduce((sum, a) => sum + a.memory, 0) + p.other_memory,
    }))
    .sort((a, b) => b.disk - a.disk);
  const figures = (d: string, m: string) => (
    <span className="grid grid-cols-[auto_auto] gap-x-3">
      <span className="text-right whitespace-nowrap">{d}</span>
      <span className="w-20 text-right whitespace-nowrap text-fg-2">{m}</span>
    </span>
  );

  return (
    <div className="space-y-6">
      <section>
        <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-3 rule-b pb-1.5">
          <h3 className="caps">{t("usage.projects")}</h3>
          <span className="caps text-right">{t("usage.disk")}</span>
          <span className="caps w-20 text-right">{t("usage.ram")}</span>
        </div>
        {projects.map(({ p, disk: total, memory: ram }) => (
          <Fold key={p.id} title={p.name} aside={figures(data.measured_at ? formatBytes(total) : "", memory(ram))}>
            <div className="pl-5">
              {p.agents.map((a) => (
                <Row key={a.id} name={a.name} disk={disk(a.disk)} memory={memory(a.memory)} />
              ))}
              <Row name={t("usage.uploads")} disk={formatBytes(p.uploads)} muted />
              {(p.other_disk > 0 || p.other_memory > 0) && <Row name={t("usage.projectOther")} disk={disk(p.other_disk)} memory={memory(p.other_memory)} muted />}
            </div>
          </Fold>
        ))}
        <p className="mt-2 text-fg-3">{t("usage.projectsHint")}</p>
      </section>

      <section>
        <Fold heading title={t("usage.shared")} aside={figures(data.measured_at ? formatBytes(sharedTotal) : "", "")}>
          {shared.map((f) => (
            <Row key={f.name} name={f.name} disk={formatBytes(f.bytes)} />
          ))}
          {sharedRest > 0 && <Row name={t("usage.sharedRest", { count: data.shared.length - SHARED_SHOWN })} disk={formatBytes(sharedRest)} muted />}
        </Fold>
        {data.other_disk > 0 && <Row name={t("usage.otherFolders")} disk={formatBytes(data.other_disk)} muted />}
        {data.memory_other > 0 && <Row name={t("usage.otherMemory")} memory={formatBytes(data.memory_other)} muted />}
        <p className="mt-2 text-fg-3">{t("usage.sharedHint")}</p>
      </section>
    </div>
  );
}

/** Every machine: the built-in runner's (with the app's data) and the external runners', one after the other. */
function Machines({ data, each }: { data: Usage; each: (machine: MachineUsage, appData?: number | null) => ReactNode }) {
  const { t } = useTranslation();
  const external = data.runners.length > 0;
  return (
    <div className="space-y-8">
      <div className="space-y-4">
        {external && <h3 className="font-extrabold">{t("runners.builtin")}</h3>}
        {each(data, data.data)}
      </div>
      {data.runners.map((r) => (
        <div key={r.id} className="space-y-4">
          <h3 className="font-extrabold">{r.name}</h3>
          <Away runner={r} />
          {(r.connected || r.known_at) && each(r)}
        </div>
      ))}
    </div>
  );
}

type Tab = "overview" | "storage" | "cleanup" | "tokens";
const TABS: Tab[] = ["overview", "storage", "cleanup", "tokens"];

/** Disk, memory, CPU and tokens of the installation, in four parts: the key figures at a glance, the
 *  storage per project and folder, what deletes or trims things (`cleanupSettings`, with their history),
 *  and the projects' tokens compared. */
export function UsageEditor({ cleanupSettings }: { cleanupSettings: ReactNode }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const usage = useUsage();
  const [tab, setTab] = useStored<Tab>("plainboard.usageTab", "overview");
  const [error, setError] = useState<unknown>(null);
  const data = usage.data;
  const current = TABS.includes(tab) ? tab : "overview";

  const measure = async () => {
    setError(null);
    try {
      qc.setQueryData(qk.usage, await api.post<Usage>("/usage/measure"));
    } catch (err) {
      setError(err);
    }
  };
  const connected = data ? [data, ...data.runners].some((m) => m.runner_ok) : false;
  const measured = data && (
    <div className="flex flex-wrap items-center gap-3">
      <Button size="sm" onClick={measure} disabled={data.measuring || !connected}>
        {t("usage.measure")}
      </Button>
      <span className="text-fg-3">
        {data.measuring ? t("usage.measuring") : data.measured_at ? t("usage.measuredAt", { when: timeAgo(data.measured_at) }) : t("usage.notMeasured")}
      </span>
      <ErrorText error={error} />
    </div>
  );
  const needsData = (content: (data: Usage) => ReactNode) => (data ? content(data) : usage.error ? <ErrorText error={usage.error} /> : <Spinner />);

  return (
    <div className="space-y-6">
      <div className="scroll-thin overflow-x-auto">
        <Segmented value={current} onChange={setTab} options={TABS.map((id) => ({ value: id, label: t(`usage.tab.${id}`) }))} aria-label={t("usage.title")} />
      </div>
      {current === "overview" &&
        needsData((data) => (
          <div className="space-y-6">
            {!data.runner_ok && <p className="text-you-fg">{t("usage.runnerDown")}</p>}
            {data.storage && <StorageTarget storage={data.storage} />}
            <Machines data={data} each={(m, appData) => <Glance data={m} appData={appData} />} />
            {measured}
          </div>
        ))}
      {current === "storage" &&
        needsData((data) => (
          <div className="space-y-6">
            {!data.runner_ok && <p className="text-you-fg">{t("usage.runnerDown")}</p>}
            <Machines data={data} each={(m) => <Storage data={m} />} />
            {measured}
          </div>
        ))}
      {current === "cleanup" && (
        <div className="space-y-8">
          {cleanupSettings}
          {data && (data.storage?.notices.length || data.cleanups.length || data.servers_stopped) ? (
            <section className="space-y-3">
              <h3 className="caps rule-b pb-1.5">{t("cleanup.history")}</h3>
              {data.storage && data.storage.notices.length > 0 && (
                <Fold title={t("cleanup.notices")} aside={<span className="text-fg-2">{data.storage.notices.length}</span>}>
                  {data.storage.notices.map((n) => (
                    <Row key={n.at} name={t(`cleanup.notice.${n.reason}`, { when: timeAgo(n.at), percent: Math.round(n.percent), projects: n.projects.join(", ") })} muted />
                  ))}
                </Fold>
              )}
              {data.cleanups.length > 0 && (
                <Fold title={t("cleanup.cleanups")} aside={<span className="text-fg-2">{data.cleanups.length}</span>}>
                  {data.cleanups.map((c) => (
                    <Cleanup key={c.at} cleanup={c} />
                  ))}
                </Fold>
              )}
              {data.servers_stopped && (
                <Fold title={t("cleanup.lastServers")} aside={<span className="text-fg-2">{timeAgo(data.servers_stopped.at)}</span>}>
                  <Cleanup cleanup={data.servers_stopped} />
                </Fold>
              )}
            </section>
          ) : null}
        </div>
      )}
      {current === "tokens" && <TokenUsage />}
    </div>
  );
}
