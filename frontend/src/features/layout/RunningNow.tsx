import clsx from "clsx";
import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAgents, useRuns, useTasks } from "../../api/queries";
import type { Run } from "../../api/types";
import { AgentDot } from "../../components/ui";
import { useSince } from "../../lib/time";
import { useShell } from "./shell-context";

/** The running turn of each agent, by agent id: where "working since" comes from. */
export function useRunningRuns(projectId: number): Map<number, Run> {
  const runs = useRuns(projectId).data ?? [];
  return new Map(runs.filter((r) => r.status === "running").map((r) => [r.agent_id, r]));
}

function useRows() {
  const { project, agents: team } = useShell();
  // A doc site's agent is listed while it works only (it is not part of the team).
  const all = useAgents(project.id).data ?? team;
  const agents = all.filter((a) => !a.doc_site_id || a.status !== "idle");
  const tasks = useTasks(project.id).data ?? [];
  const running = useRunningRuns(project.id);
  const since = useSince();
  return agents.map((agent) => {
    const run = running.get(agent.id);
    const task = tasks.find((x) => (run?.task_id ? x.id === run.task_id : agent.is_manager ? x.manager_active : x.active_agent_ids.includes(agent.id)));
    return { agent, ticket: agent.status !== "idle" && task ? `#${task.number}` : "", time: run ? since(run.started_at ?? run.created_at) : "" };
  });
}

/** Who is working on what and for how long: a table with fixed columns (sidebar) or one scrolling
 * line (phone). A row opens the chat with that agent. */
export function RunningNow({ variant, onTight }: { variant: "table" | "strip"; onTight?: (tier: number) => void }) {
  const { t } = useTranslation();
  const { openChat } = useShell();
  const rows = useRows();

  if (variant === "strip") {
    const busy = rows.filter((row) => row.agent.status !== "idle");
    if (!busy.length) return null;
    return (
      <div className="flex shrink-0 items-center gap-4 overflow-x-auto px-4 pb-2.5 whitespace-nowrap text-fg-2 [scrollbar-width:none]">
        {busy.map(({ agent, ticket, time }) => (
          <button key={agent.id} type="button" onClick={() => openChat(agent.id)} className="inline-flex items-center gap-1.5" title={t("shell.openChat", { name: agent.name })}>
            <AgentDot status={agent.status} />
            {[agent.name, ticket].filter(Boolean).join(" ")}
            {time && ` · ${time}`}
          </button>
        ))}
      </div>
    );
  }
  return <RunningTable rows={rows} onTight={onTight} />;
}

type Row = ReturnType<typeof useRows>[number];

/** The sidebar table never scrolls and never gets cut off. With the height for it, it lists everyone
 * (busy first); with less it keeps as many busy rows as fit plus one "+N more" line (busy and idle
 * together); at the very least it is one line, "N running". When even that does not fit beside the
 * full Landing block, Landing shrinks step by step (`onTight` reports the tier). Everything is measured, never guessed. */
function RunningTable({ rows, onTight }: { rows: Row[]; onTight?: (tier: number) => void }) {
  const { t } = useTranslation();
  const { openChat } = useShell();
  const section = useRef<HTMLElement>(null);
  const probe = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(Infinity);
  const [tier, setTier] = useState(0);
  const sorted = [...rows].sort((a, b) => Number(a.agent.status === "idle") - Number(b.agent.status === "idle"));
  const busy = sorted.filter((row) => row.agent.status !== "idle");

  useLayoutEffect(() => {
    const me = section.current;
    const stack = me?.closest<HTMLElement>("[data-part=side-stack]");
    if (!me || !stack) return;
    const measure = () => {
      const head = (probe.current?.firstElementChild as HTMLElement | null)?.offsetHeight ?? 0;
      const rowH = sorted.length ? ((probe.current?.offsetHeight ?? 0) - head) / sorted.length : 33;
      const leaves = [...stack.children].flatMap((c) => (c.hasAttribute("data-stack-end") ? [...c.children] : [c])).filter((c) => c !== me) as HTMLElement[];
      const gap = parseFloat(getComputedStyle(stack).rowGap) || 0;
      const used = leaves.reduce((sum, c) => sum + c.offsetHeight, 0) + gap * leaves.length;
      const flow = leaves.find((c) => c.matches("[data-part=flow-side]"));
      const flowFull = flow?.querySelector<HTMLElement>("[data-flow-probe]")?.offsetHeight ?? flow?.offsetHeight ?? 0;
      // Tiers: 0 everything; 1 Landing on one line; 2 Landing without its heading; 3 Running now without its heading too; 4 Landing gone.
      const total = stack.clientHeight - (used - (flow?.offsetHeight ?? 0));
      const flowH = [flowFull, head + rowH, rowH, rowH, -gap];
      const runMin = [head + rowH, head + rowH, head + rowH, rowH, rowH];
      const found = [0, 1, 2, 3].find((i) => total >= flowH[i] + runMin[i]);
      const next = flow ? (found ?? 4) : total >= head + rowH ? 0 : 3;
      const avail = total - (flow ? flowH[next] : 0);
      setFit(Math.floor((avail - (next < 3 ? head : 0)) / rowH));
      setTier(next);
      onTight?.(next);
    };
    measure();
    const ro = new ResizeObserver(measure);
    const watch = () => {
      ro.disconnect();
      ro.observe(stack);
      if (probe.current) ro.observe(probe.current);
      stack.querySelectorAll(":scope > *, :scope > [data-stack-end] > *, [data-flow-probe]").forEach((c) => c !== me && ro.observe(c));
    };
    watch();
    const mo = new MutationObserver(watch);
    mo.observe(stack, { childList: true, subtree: true });
    return () => {
      ro.disconnect();
      mo.disconnect();
    };
  }, [rows.length, onTight]);

  const total = sorted.length;
  const line = "block w-full border-b border-line py-[7px] text-left text-fg-3";
  let shown: Row[] = sorted;
  let note = "";
  if (fit < total) {
    if (busy.length + (total > busy.length ? 1 : 0) <= fit) {
      shown = busy;
      note = t("shell.idleMore", { count: total - busy.length });
    } else if (fit >= 2) {
      shown = busy.slice(0, fit - 1);
      note = t("shell.moreRows", { count: total - shown.length });
    } else {
      shown = [];
      note = busy.length ? t("shell.runningCount", { count: busy.length }) : t("shell.nobodyWorking");
    }
  }
  return (
    <section ref={section} className="relative flex shrink-0 flex-col px-3.5" data-part="running-now">
      {tier < 3 && <h2 className="caps shrink-0 rule-b pb-1.5">{t("shell.runningNow")}</h2>}
      <div>
        {shown.map((row) => (
          <RowLine key={row.agent.id} row={row} onOpen={() => openChat(row.agent.id)} />
        ))}
        {note && <p className={line}>{note}</p>}
      </div>
      {/* Invisible copy of the full list: its height is what "enough room" is measured against. */}
      <div ref={probe} aria-hidden className="pointer-events-none invisible absolute inset-x-3.5 top-0 -z-10">
        <h2 className="caps rule-b pb-1.5">{t("shell.runningNow")}</h2>
        {sorted.map((row) => (
          <RowLine key={row.agent.id} row={row} />
        ))}
      </div>
    </section>
  );
}

function RowLine({ row: { agent, ticket, time }, onOpen }: { row: Row; onOpen?: () => void }) {
  const { t } = useTranslation();
  const idle = agent.status === "idle";
  const cls = "grid w-full grid-cols-[9px_minmax(0,1fr)_auto_auto] items-center gap-x-2 border-b border-line py-[7px] text-left hover:bg-sunk";
  const cells = (
    <>
      <AgentDot status={agent.status} />
      <span className={clsx("truncate", idle && "text-fg-3")}>{agent.name}</span>
      <span className="text-fg-2">{ticket}</span>
      <span className={clsx("min-w-7 text-right", idle ? "text-fg-3" : "text-fg-2")}>{idle ? t("agentStatus.idle").toLowerCase() : time}</span>
    </>
  );
  return onOpen ? (
    <button type="button" onClick={onOpen} title={[t("shell.openChat", { name: agent.name }), t(`agentStatus.${agent.status}`), ...agent.job_commands].join("\n")} className={cls}>
      {cells}
    </button>
  ) : (
    <div className={cls}>{cells}</div>
  );
}
