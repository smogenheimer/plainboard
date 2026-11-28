import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowUp, ChevronDown, CircleHelp, Eye, EyeOff, Loader2, Maximize2, MoreHorizontal, Paperclip, Square, Trash2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import { useCopying, useStreamingText } from "../../api/events";
import { qk, useConversation, useMe, useRuns, useSettings } from "../../api/queries";
import type { Attachment, ChatPart, Conversation } from "../../api/types";
import { Avatar } from "../../components/Avatar";
import { Markdown } from "../../components/Markdown";
import { PendingFile } from "../../components/PendingFile";
import { useMentionPicker, useReadMentionsOf } from "../../components/Mentions";
import { AgentSquare, ChatPanelToggle, SwitchTrack, IconButton, Select, Spinner } from "../../components/ui";
import { formatCost, formatTokens } from "../../lib/format";
import { sendsOnEnter, useAutoGrow, useDismiss, useInView, useStored } from "../../lib/hooks";
import { useNow } from "../../lib/time";
import { seedChat, useChatSeed, useDraft, useDraftFiles } from "../../lib/drafts";
import { useChatOutbox } from "./chatOutbox";
import { useShell } from "../layout/shell-context";
import { AskDialog } from "./AskDialog";
import { LimitsRing } from "./LimitsRing";
import { CHAT_ROW, Head, MessageItem, usePeople, writtenParts } from "./MessageItem";
import { StoppingAgents } from "./StoppingAgents";

const GROUP_MS = 5 * 60_000;

export function ChatPanel({
  agentId,
  onSelectAgent,
  onClose,
  onExpand,
  variant = "panel",
}: {
  agentId: number;
  onSelectAgent: (id: number) => void;
  onClose?: () => void;
  onExpand?: () => void;
  variant?: "panel" | "page";
}) {
  const { t } = useTranslation();
  const { project, agents, agentById, canWrite, openSettings } = useShell();
  const qc = useQueryClient();
  const agent = agentById(agentId);
  const settings = useSettings();
  const me = useMe();
  const conversation = useConversation(agentId);
  const runs = useRuns(project.id);
  const streaming = useStreamingText(agentId);
  const copying = useCopying(project.id);
  const people = usePeople();
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  // The row at the top of the view and its distance from the end of the content, so what changes
  // below it (live text, new messages, a picture that loads) doesn't move what one reads.
  const anchor = useRef<{ row: Element; fromEnd: number } | null>(null);
  const rest = useRef(0);
  const fetchingOlder = useRef(false);
  const [details, setDetails] = useStored<boolean>("plainboard.chatDetails", false);
  const [files, setFiles] = useDraftFiles(`chat:${agentId}`);
  const [seed, setSeed] = useState<{ text: string; n: number } | null>(null);
  // A request prepared elsewhere (openChat with a text): into the field, once.
  const prepared = useChatSeed(agentId);
  useEffect(() => {
    if (!prepared) return;
    setSeed((last) => ({ text: prepared.text, n: (last?.n ?? 0) + 1 }));
    seedChat(agentId, null);
  }, [prepared, agentId]);

  const activeRuns = (runs.data ?? []).filter((r) => r.agent_id === agentId && (r.status === "queued" || r.status === "running"));
  const running = activeRuns.find((r) => r.status === "running");
  const queued = activeRuns.filter((r) => r.status === "queued" && r.trigger === "chat");
  const messages = conversation.data?.messages ?? [];
  // Sent messages the server has (by the sender's key): the outbox lets go of them, also before or
  // without the answer to their request (#179).
  const arrivedKeys = [
    ...(runs.data ?? []).filter((r) => r.agent_id === agentId).flatMap((r) => r.parts ?? []),
    ...messages.flatMap((m) => (m.role === "user" && Array.isArray(m.meta?.parts) ? (m.meta.parts as ChatPart[]) : [])),
  ]
    .map((p) => p.key)
    .filter((key): key is string => !!key)
    .join(" ");
  const arrived = useMemo(() => new Set(arrivedKeys ? arrivedKeys.split(" ") : []), [arrivedKeys]);
  // A message the agent has not read yet stays at the very bottom, below whatever the agent still
  // writes, in the order it was sent. Once read it takes its place: at the time it was read, above
  // what the agent wrote after that. (By time, not by id: a busy agent can read a message and react
  // to it before the turn that answers it starts.)
  const unread = queued.filter((r) => !r.read_at);
  const timeline = [
    ...messages.map((m) => ({ at: Date.parse(m.created_at), message: m, run: null })),
    ...queued.filter((r) => r.read_at).map((r) => ({ at: Date.parse(r.read_at!), message: null, run: r })),
  ]
    .map((item, index) => ({ ...item, index }))
    .sort((a, b) => a.at - b.at || a.index - b.index);
  // What the agent writes in one go reads as one answer: the name stands on its first part only.
  const said = (m: (typeof messages)[number] | null) => !!m && m.role !== "user" && (m.kind === "text" || m.kind === "files");
  const shown = timeline.filter(({ message }) => !message || details || (message.kind !== "tool" && message.kind !== "summary"));
  const follows = new Set(
    shown.filter((item, i) => i > 0 && said(item.message) && said(shown[i - 1].message) && item.at - shown[i - 1].at < GROUP_MS).map((item) => item.message!.id),
  );
  // Works only until the agent's turn starts; if it just did, the refreshed list shows the message as sent.
  const deleteWaiting = (runId: number, part?: number) =>
    api
      .del(`/runs/${runId}/message${part ? `?part=${part}` : ""}`)
      .catch(() => undefined)
      .then(() => qc.invalidateQueries({ queryKey: qk.runs(project.id) }));

  // Messages that wait for the same turn go out together, but each shows on its own, with its writer.
  const waitingRow = (r: (typeof queued)[number]) =>
    (r.parts ?? [{ id: 0, author: r.author_name, text: r.input_text, attachment_ids: r.attachment_ids, at: "" }]).map((p) => (
      <div key={`run-${r.id}-${p.id}`} data-part="msg" data-role="queued" className={clsx(CHAT_ROW, "bg-sunk")}>
        <Head
          name={p.author ?? t("chat.you")}
          at={p.at || undefined}
          avatar={p.author ? <Avatar name={p.author} person={people.get(p.author.toLowerCase())} /> : undefined}
        >
          <span className="mk mk-plan self-center" />
          <span className="min-w-0 flex-1 truncate text-fg-3">{t(r.read_at ? "chat.readWaiting" : "chat.waiting", { name: agent?.name ?? "" })}</span>
          {!r.read_at && (
            <IconButton label={t("chat.deleteWaiting")} onClick={() => deleteWaiting(r.id, p.id)} className="-my-1 self-center">
              <Trash2 className="size-4" />
            </IconButton>
          )}
        </Head>
        <div className="break-words whitespace-pre-wrap text-fg-2">
          {r.parts
            ? p.text
            : writtenParts(r.input_text, r.author_name, people)
                .map((part) => (part.author && part.author !== r.author_name ? `${part.author}: ${part.text}` : part.text))
                .join("\n")}
        </div>
        {p.attachment_ids.length > 0 && (
          <span className="mt-1 flex items-center gap-1 text-fg-3">
            <Paperclip className="size-4" />
            {t("chat.fileCount", { count: p.attachment_ids.length })}
          </span>
        )}
      </div>
    ));

  // The scroller is reversed (column-reverse): its offset counts from the end, so older messages put
  // on top keep the view still by themselves. Only a change below the anchor row moves the view; it is
  // measured against the content, not against the scroll offset, so a scroll whose event has not
  // arrived yet (touch momentum) is never undone, and scrollTop is only touched when something moved.
  const keepView = () => {
    const el = scroller.current;
    const inner = content.current;
    if (!el || !inner) return;
    // At the end the reversed scroller keeps the end in view by itself. Setting scrollTop here would
    // pull every scroll that starts upwards back down while it is still within reach of the end.
    if (stickToBottom.current) {
      anchor.current = null;
      rest.current = 0;
      return;
    }
    if (anchor.current?.row.isConnected) {
      // Some browsers keep whole pixels only: what is left over is made up next time.
      const moved = inner.getBoundingClientRect().bottom - anchor.current.row.getBoundingClientRect().top - anchor.current.fromEnd + rest.current;
      rest.current = 0;
      if (Math.abs(moved) >= 1) {
        const before = el.scrollTop;
        el.scrollTop = before - moved;
        const left = moved - (before - el.scrollTop);
        rest.current = Math.abs(left) < 1 ? left : 0;
      } else rest.current = moved;
    }
    const top = el.getBoundingClientRect().top;
    const row = Array.from(inner.querySelectorAll("[data-part]")).find((r) => r.getBoundingClientRect().bottom > top);
    anchor.current = row ? { row, fromEnd: inner.getBoundingClientRect().bottom - row.getBoundingClientRect().top } : null;
  };

  useLayoutEffect(keepView, [messages, streaming, queued.length, agentId, activeRuns.length]);

  // Heights also change on their own: pictures load, diagrams render, details open.
  useEffect(() => {
    const inner = content.current;
    if (!inner) return;
    const observer = new ResizeObserver(() => keepView());
    observer.observe(inner);
    return () => observer.disconnect();
  }, [!!agent]);

  useLayoutEffect(() => {
    stickToBottom.current = true;
    if (scroller.current) scroller.current.scrollTop = 0;
  }, [agentId]);

  // Older messages are fetched while the reader is still a few screens below the top, page after
  // page, so scrolling up never runs into the end of what is loaded.
  const loadOlder = () => {
    const el = scroller.current;
    const conv = conversation.data;
    if (!el || !conv?.has_more || !conv.messages.length || fetchingOlder.current) return;
    if (el.scrollHeight - el.clientHeight + el.scrollTop > Math.max(2000, 3 * el.clientHeight)) return;
    fetchingOlder.current = true;
    const id = agentId;
    const before = conv.messages[0].id;
    api
      .get<Conversation>(`/agents/${id}/conversation?before_id=${before}&limit=100`)
      .then((older) =>
        qc.setQueryData<Conversation>(qk.conversation(id), (cur) =>
          cur && cur.messages[0]?.id === before ? { ...cur, messages: [...older.messages, ...cur.messages], has_more: older.has_more } : cur,
        ),
      )
      .catch(() => undefined)
      .finally(() => {
        fetchingOlder.current = false;
      });
  };

  useEffect(loadOlder, [conversation.data]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    stickToBottom.current = -el.scrollTop < 80;
    // Back at the bottom, what was loaded far above is let go again: it comes back the same way.
    const conv = conversation.data;
    if (stickToBottom.current && conv && conv.messages.length > 400 && !fetchingOlder.current)
      qc.setQueryData<Conversation>(qk.conversation(agentId), { ...conv, messages: conv.messages.slice(-100), has_more: true });
    // A change that came in since the last look still counts: scrolling itself doesn't move the anchor.
    keepView();
    loadOlder();
  };

  if (!agent) return null;
  // On the full page, keep a comfortable reading width.
  const membersOff = !!agent && !agent.is_manager && !!settings.data && !settings.data.values.member_chat;
  const column = variant === "page" ? "mx-auto w-full max-w-3xl" : "";
  const contextText = `${formatTokens(conversation.data?.context_tokens ?? agent.context_tokens)} / ${formatTokens(agent.context_limit_tokens)}`;
  const hasFiles = (e: DragEvent) => e.dataTransfer.types.includes("Files");
  const onDragOver = (e: DragEvent) => {
    if (hasFiles(e)) e.preventDefault();
  };
  const onDrop = (e: DragEvent) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (agent.enabled) setFiles((cur) => [...cur, ...Array.from(e.dataTransfer.files)]);
  };

  return (
    <div className="flex h-full min-h-0 flex-col" onDragOver={onDragOver} onDrop={onDrop}>
      <header className={clsx("flex h-12 shrink-0 items-center gap-0.5 border-b border-line pl-3", onClose ? "pr-4" : "pr-2")}>
        <div className="min-w-0 flex-1">
          <Select
            value={agentId}
            onChange={(e) => onSelectAgent(Number(e.target.value))}
            aria-label={t("chat.selectAgent")}
            trigger={
              <span className="flex h-8 min-w-0 items-center gap-2" title={`${agent.role} · ${agent.model}`}>
                <AgentSquare agent={agent} />
                <span className="truncate font-extrabold">{agent.name}</span>
                <ChevronDown className="size-3.5 shrink-0 text-fg-3" />
              </span>
            }
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} — {a.role}
              </option>
            ))}
          </Select>
        </div>
        {details && <ContextGauge used={conversation.data?.context_tokens ?? agent.context_tokens} limit={agent.context_limit_tokens} base={agent.base_context_tokens} />}
        <ChatMenu agentId={agentId} cost={agent.total_cost_usd} context={contextText} disabled={!!running || !canWrite} details={details} onDetails={setDetails} />
        {onExpand && (
          <IconButton label={t("chat.expand")} onClick={onExpand}>
            <Maximize2 className="size-4" />
          </IconButton>
        )}
        {onClose && <ChatPanelToggle where="chat" />}
      </header>

      <div ref={scroller} onScroll={onScroll} className="scroll-thin flex min-h-0 flex-1 flex-col-reverse overflow-x-hidden overflow-y-auto [overflow-anchor:none]">
        <div ref={content} className={clsx(column, "mb-auto")}>
        {conversation.isLoading && (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        )}
        {conversation.data?.has_more && (
          <div className="flex justify-center py-3" aria-label={t("common.loading")}>
            <Spinner />
          </div>
        )}
        {!conversation.isLoading && messages.length === 0 && !queued.length && (
          <ChatIntro isManager={agent.is_manager} onPick={canWrite && agent.enabled ? (text) => setSeed((last) => ({ text, n: (last?.n ?? 0) + 1 })) : undefined} />
        )}
        {timeline.map(({ message, run: r }) =>
          message ? <MessageItem key={message.id} message={message} agent={agent} details={details} follows={follows.has(message.id)} /> : r && waitingRow(r),
        )}
        {running && streaming && (
          <div data-part="msg" data-role="agent" data-follows={said(shown[shown.length - 1]?.message ?? null) ? "" : undefined} className={CHAT_ROW}>
            <Head name={agent.name} />
            <Markdown>{streaming}</Markdown>
          </div>
        )}
        {unread.map(waitingRow)}
        </div>
      </div>

      {membersOff ? (
        <MembersOffHint className={column} isAdmin={!!me?.is_admin} onOpenSettings={() => openSettings("general")} />
      ) : !canWrite ? (
        <div className="shrink-0 border-t border-line px-3 py-3 text-fg-3">
          <p className={column}>{t("members.readOnlyShort")}</p>
        </div>
      ) : (
      <Composer
        seed={seed}
        agentId={agentId}
        agentName={agent.name}
        runningId={running?.id}
        disabled={!agent.enabled}
        className={column}
        status={
          activeRuns.length > 0
            ? running && copying
              ? t("chat.copying", { name: agent.name, done: copying.done, total: copying.total })
              : t(running ? "chat.working" : "chat.waiting", { name: agent.name })
            : agent.running_jobs > 0
              ? t("chat.jobsRunning", { count: agent.running_jobs })
              : undefined
        }
        statusTitle={activeRuns.length === 0 ? agent.job_commands.join("\n") : undefined}
        runningSince={running ? (running.started_at ?? running.created_at) : undefined}
        files={files}
        onFilesChange={setFiles}
        arrived={arrived}
      />
      )}
    </div>
  );
}

/** Writing to a member is off (settings): the field gives way to a quiet note. */
function MembersOffHint({ className, isAdmin, onOpenSettings }: { className?: string; isAdmin: boolean; onOpenSettings: () => void }) {
  const { t } = useTranslation();
  return (
    <div data-part="composer-off" className="shrink-0 border-t border-line px-3 py-3 text-fg-3">
      <p className={className}>
        {t("chat.membersOff")}{" "}
        {isAdmin && (
          <button type="button" onClick={onOpenSettings} className="text-fg-2 underline underline-offset-2 hover:text-fg touch:py-2">
            {t("chat.membersOffSettings")}
          </button>
        )}
      </p>
    </div>
  );
}

const STARTERS = ["build", "existing", "change"] as const;

/** An empty chat says what it is for. With the manager it also offers a few ways to begin: a click
 * puts a sentence to complete into the message field, nothing is sent. */
function ChatIntro({ isManager, onPick }: { isManager: boolean; onPick?: (text: string) => void }) {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-sm space-y-2 px-4 py-10 text-fg-2">
      <p className="text-center font-extrabold text-fg">{isManager ? t("chat.introManagerTitle") : t("chat.introAgentTitle")}</p>
      <p className="text-center">{isManager ? t("chat.introManager") : t("chat.introAgent")}</p>
      {isManager && onPick && (
        <div className="pt-4">
          <p className="pb-1 text-fg-3">{t("chat.starters.hint")}</p>
          <ul className="border-t border-line">
            {STARTERS.map((id) => (
              <li key={id} className="border-b border-line">
                <button type="button" onClick={() => onPick(t(`chat.starters.${id}.text`))} className="block w-full px-1 py-2 text-left text-fg hover:bg-sunk touch:py-3">
                  {t(`chat.starters.${id}.label`)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ContextGauge({ used, limit, base }: { used: number; limit: number; base: number }) {
  const { t } = useTranslation();
  const ratio = limit ? Math.min(1, used / limit) : 0;
  return (
    <span
      className={clsx("mr-1.5 hidden whitespace-nowrap sm:inline", ratio < 0.9 ? "text-fg-3" : "font-semibold text-fg")}
      title={t("chat.contextTitle", { used: formatTokens(used), limit: formatTokens(limit), base: formatTokens(base) })}
    >
      {formatTokens(used)} / {formatTokens(limit)}
    </span>
  );
}

/** What one rarely needs for a conversation: details on/off, compress, fresh session, context and cost. */
function ChatMenu({
  agentId,
  cost,
  context,
  disabled,
  details,
  onDetails,
}: {
  agentId: number;
  cost: number;
  context: string;
  disabled: boolean;
  details: boolean;
  onDetails: (on: boolean) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [menuRef, inView] = useInView<HTMLDivElement>(open);
  useDismiss(ref, open, () => setOpen(false));

  const action = async (name: "compress" | "reset-session") => {
    setBusy(name);
    setNote(null);
    try {
      const res = await api.post<Record<string, boolean>>(`/agents/${agentId}/${name}`);
      if (name === "compress" && !res.compressed) setNote(t("chat.nothingToCompress"));
      else setOpen(false);
      qc.invalidateQueries({ queryKey: qk.conversation(agentId) });
    } catch (err) {
      setNote(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div ref={ref} className="relative">
      <IconButton label={t("chat.menu")} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} className={clsx(open && "bg-sunk text-fg")}>
        <MoreHorizontal className="size-4" />
      </IconButton>
      {open && (
        <div ref={menuRef} style={inView} data-part="menu" className="absolute top-full right-0 z-40 mt-1 w-64 max-w-[calc(100vw-1rem)] rounded-sm bg-bg p-1 shadow-lg ring-1 ring-line-2">
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={details}
            onClick={() => onDetails(!details)}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-2 text-left font-semibold hover:bg-sunk touch:py-2.5"
          >
            {details ? <Eye className="size-4 shrink-0" /> : <EyeOff className="size-4 shrink-0 text-fg-3" />}
            <span className="flex-1">{t("chat.details")}</span>
            <SwitchTrack checked={details} />
          </button>
          <div className="my-1 border-t border-line" />
          <MenuItem label={t("chat.compressNow")} hint={t("chat.compressHint")} busy={busy === "compress"} disabled={disabled} onClick={() => action("compress")} />
          <MenuItem label={t("chat.resetSession")} hint={t("chat.resetHint")} busy={busy === "reset-session"} disabled={disabled} onClick={() => action("reset-session")} />
          <div className="border-t border-line px-2 py-1.5 text-fg-3">{t("chat.context", { context })} · {t("chat.totalCost", { cost: formatCost(cost) })}</div>
          {note && <div className="px-2 pb-1.5 text-you-fg">{note}</div>}
        </div>
      )}
    </div>
  );
}

function MenuItem({ label, hint, onClick, busy, disabled }: { label: string; hint: string; onClick: () => void; busy: boolean; disabled: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled || busy} className="block w-full rounded-sm px-2 py-1.5 text-left hover:bg-sunk disabled:opacity-50">
      <div className="flex items-center gap-1.5 font-semibold">
        {busy && <Loader2 className="size-4 animate-spin" />}
        {label}
      </div>
      <div className="text-fg-3">{hint}</div>
    </button>
  );
}

/** Minutes and seconds since a turn started, counting on screen. */
function Elapsed({ since }: { since: string }) {
  const seconds = Math.max(0, Math.floor((useNow(1000) - Date.parse(since)) / 1000));
  return (
    <time className="shrink-0 font-normal text-fg-3">
      {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
    </time>
  );
}

function Composer({
  seed,
  agentId,
  agentName,
  runningId,
  disabled,
  className,
  status,
  statusTitle,
  runningSince,
  files,
  onFilesChange,
  arrived,
}: {
  /** A text to start from, put into the field when it changes. */
  seed?: { text: string; n: number } | null;
  agentId: number;
  agentName: string;
  runningId?: number;
  disabled: boolean;
  className?: string;
  status?: string;
  statusTitle?: string;
  runningSince?: string;
  files: File[];
  onFilesChange: (files: File[]) => void;
  /** Keys of sent messages the server has. */
  arrived: Set<string>;
}) {
  const { t } = useTranslation();
  const { project } = useShell();
  const qc = useQueryClient();
  const [text, setText] = useDraft(`chat:${agentId}`);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // A question instead of a message (Ctrl+Enter or the ? button): answered in a window of its own.
  const [asking, setAsking] = useState<string | null>(null);
  const shortcut = /Mac|iPhone|iPad/.test(navigator.userAgent) ? "⌘+Enter" : "Ctrl+Enter";
  const area = useRef<HTMLTextAreaElement>(null);
  const mention = useMentionPicker(project.id, text, setText, area);
  useReadMentionsOf({ agentId });
  useEffect(() => {
    if (!seed) return;
    setText(seed.text);
    area.current?.focus();
  }, [seed]);
  const picker = useRef<HTMLInputElement>(null);

  useAutoGrow(area, text);

  // What was sent waits here until the server has it: across a reload and a restart of the server.
  const me = useMe();
  const { outbox, items, waiting } = useChatOutbox(me?.id ?? 0, agentId, () => qc.invalidateQueries({ queryKey: qk.runs(project.id) }));
  const outgoing = items.filter((i) => !arrived.has(i.key));
  useEffect(() => {
    outbox.settle(arrived);
  }, [outbox, items, arrived]);
  useEffect(() => {
    const rejected = outbox.takeRejected();
    if (!rejected) return;
    setText((cur) => (cur.trim() ? `${rejected.text}\n\n${cur}` : rejected.text));
    setError(rejected.error ?? null);
  }, [outbox, items]);

  const takeBack = (key: string) => {
    const item = outgoing.find((i) => i.key === key);
    if (!item) return;
    outbox.remove(key);
    setText((cur) => (cur.trim() ? `${item.text}\n\n${cur}` : item.text));
  };

  const send = async () => {
    const value = text.trim();
    const pending = files;
    if ((!value && !pending.length) || sending) return;
    setError(null);
    setText("");
    onFilesChange([]);
    if (!pending.length) {
      outbox.add(value, []);
      return;
    }
    setSending(true);
    try {
      const form = new FormData();
      for (const file of pending) form.append("files", file, file.name || "pasted.png");
      const attachment_ids = (await api.post<Attachment[]>(`/agents/${agentId}/attachments`, form)).map((a) => a.id);
      outbox.add(value, attachment_ids);
    } catch (err) {
      // Files can't wait in the browser's storage: they stay in the field until sent again.
      setText(value);
      onFilesChange(pending);
      setError(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const askQuestion = () => {
    const value = text.trim();
    if (!value || asking !== null) return;
    setText("");
    setAsking(value);
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    // Office apps also put a picture of copied text on the clipboard: text wins.
    const pasted = Array.from(e.clipboardData.files);
    if (pasted.length && !e.clipboardData.getData("text/plain")) {
      e.preventDefault();
      onFilesChange([...files, ...pasted]);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention.onKeyDown(e)) return;
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) {
      e.preventDefault();
      askQuestion();
    } else if (sendsOnEnter(e)) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="shrink-0 border-t border-line px-3 pt-2.5 pb-3">
      <div className={className}>
      {error && <p className="mb-2 text-you-fg">{error}</p>}
      {waiting && outgoing.some((i) => !i.error) && (
        <div role="status" data-part="outbox" className="mb-2 flex flex-col gap-1">
          <p className="flex items-center gap-2 font-semibold">
            <Loader2 className="size-4 shrink-0 motion-safe:animate-spin" />
            {t("chat.waitingForServer")}
          </p>
          {outgoing
            .filter((i) => !i.error)
            .map((i) => (
              <div key={i.key} className="flex items-center gap-2 text-fg-2">
                <span className="min-w-0 flex-1 truncate">{i.text || t("chat.filesOnly", { count: i.attachment_ids.length })}</span>
                <IconButton label={t("chat.takeBack")} onClick={() => takeBack(i.key)} className="size-7 touch:size-11">
                  <X className="size-3.5" />
                </IconButton>
              </div>
            ))}
        </div>
      )}
      <StoppingAgents />
      {status && (
        <div role="status" title={statusTitle || undefined} className="mb-2 flex items-center gap-2 font-semibold">
          <span className={clsx("mk", runningId ? "mk-run motion-safe:animate-pulse" : "mk-plan")} />
          <span className="min-w-0 flex-1 truncate">{status}</span>
          {runningSince && <Elapsed since={runningSince} />}
        </div>
      )}
      {files.length > 0 && (
        <div className="mb-2 flex flex-wrap items-end gap-2">
          {files.map((file, i) => (
            <PendingFile key={`${file.name}-${file.lastModified}-${i}`} file={file} onRemove={() => onFilesChange(files.filter((_, j) => j !== i))} />
          ))}
        </div>
      )}
      <div data-part="composer" className="relative flex items-end gap-1 rounded-sm p-1 ring-1 ring-line-2 ring-inset focus-within:ring-2 focus-within:ring-fg">
        {mention.menu}
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            onFilesChange([...files, ...Array.from(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        <IconButton label={t("chat.attachFiles")} disabled={disabled} onClick={() => picker.current?.click()} className="size-8 touch:size-11">
          <Paperclip className="size-4" />
        </IconButton>
        <textarea
          ref={area}
          rows={1}
          value={text}
          disabled={disabled}
          onChange={(e) => {
            setText(e.target.value);
            mention.track();
          }}
          onSelect={mention.track}
          onKeyDown={onKey}
          onPaste={onPaste}
          placeholder={disabled ? t("chat.agentDisabled") : runningId ? t("chat.queued") : t("chat.placeholder", { name: agentName })}
          aria-label={t("chat.placeholder", { name: agentName })}
          className="scroll-thin max-h-[200px] min-h-8 min-w-0 flex-1 resize-none bg-transparent placeholder:truncate px-1.5 py-1.5 outline-none touch:min-h-11 touch:py-2.5"
        />
        {runningId ? (
          <IconButton label={t("chat.stop")} onClick={() => api.post(`/runs/${runningId}/cancel`)} className="size-8 text-fg-2 ring-1 ring-line-2 ring-inset touch:size-11">
            <Square className="size-3.5 fill-current" />
          </IconButton>
        ) : null}
        <IconButton
          label={t("ask.button", { shortcut })}
          data-part="ask"
          disabled={disabled}
          onClick={() => {
            if (text.trim()) askQuestion();
            else area.current?.focus();
          }}
          className="size-8 touch:size-11"
        >
          <CircleHelp className="size-4" />
        </IconButton>
        <button
          onClick={send}
          disabled={(!text.trim() && !files.length) || disabled || sending}
          aria-label={runningId ? t("chat.queued") : t("chat.send")}
          title={runningId ? t("chat.queued") : undefined}
          data-part="send"
          className="plate flex size-8 shrink-0 items-center justify-center rounded-sm transition-colors hover:bg-fg-2 disabled:bg-line disabled:text-fg-3 touch:size-11"
        >
          <ArrowUp className="size-4" />
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="ml-auto flex flex-wrap items-center justify-end gap-x-2">
          <LimitsRing />
        </div>
      </div>
      </div>
      {asking !== null && <AskDialog projectId={project.id} question={asking} onClose={() => setAsking(null)} />}
    </div>
  );
}
