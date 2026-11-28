import clsx from "clsx";
import { AlertCircle, Archive, Bell, CalendarClock, ChevronRight, ClipboardList, HelpCircle, Mail, Paperclip, RefreshCw, Ticket, Wrench } from "lucide-react";
import { memo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useDirectory, useMe } from "../../api/queries";
import type { Agent, Message, Person } from "../../api/types";
import { AttachmentLink } from "../../components/AttachmentLink";
import { Avatar } from "../../components/Avatar";
import { Lightbox } from "../../components/Lightbox";
import { VideoThumb } from "../../components/VideoThumb";
import { isMediaFile, isVideoFile, thumbSrc } from "../../lib/media";
import { Markdown, TicketText } from "../../components/Markdown";
import { chatTime, formatDateTime, formatTokens } from "../../lib/format";
import { CodeLine, useShowCode } from "./ToolCode";
import { useShell } from "../layout/shell-context";

/** Every entry of the chat is a ruled row with the same inset. */
export const CHAT_ROW = "border-b border-line px-4 py-2.5";
const LINK = "shrink-0 underline decoration-line-2 underline-offset-[3px] hover:decoration-current";

/** `details` shows tool activity, compression markers and full event cards; otherwise the chat
 * stays clean (messages only, events as one-line notes). `follows`: the agent wrote the message
 * before it too, a moment ago, so a design may leave the name out. */
export const MessageItem = memo(function MessageItem({ message, agent, details, follows = false }: { message: Message; agent: Agent; details: boolean; follows?: boolean }) {
  switch (message.kind) {
    case "tool":
      return details ? <ToolStep message={message} /> : null;
    case "summary":
      return details ? <SummaryDivider message={message} /> : null;
    case "error":
      return (
        <div data-part="msg" data-role="error" className={clsx(CHAT_ROW, "flex items-start gap-2 bg-you-bg text-you-fg")}>
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <span className="min-w-0 break-words whitespace-pre-wrap">
            <TicketText text={message.content} />
          </span>
        </div>
      );
  }
  if (message.role === "user") return <UserMessage message={message} details={details} />;
  if (message.kind === "files")
    return (
      <div data-part="msg" data-role="agent" data-follows={follows ? "" : undefined} className={CHAT_ROW}>
        <Head name={agent.name} at={message.created_at} />
        <MessageFiles files={message.meta.attachments ?? []} />
      </div>
    );
  return (
    <div data-part="msg" data-role="agent" data-follows={follows ? "" : undefined} className={CHAT_ROW}>
      <Head name={agent.name} at={message.created_at} />
      <Markdown>{message.content}</Markdown>
    </div>
  );
});

/** Who wrote the row, and when: a person's picture and the name at the left, time in a fixed place at the right. */
export function Head({ name, at, avatar, children }: { name: string; at?: string; avatar?: ReactNode; children?: ReactNode }) {
  return (
    <div data-part="msg-head" className="mb-0.5 flex items-baseline gap-2">
      {avatar && <span className="flex shrink-0 self-center">{avatar}</span>}
      <b className="min-w-0 truncate font-semibold">{name}</b>
      {children}
      {at && <MessageTime at={at} />}
    </div>
  );
}

function MessageTime({ at }: { at: string }) {
  return (
    <time dateTime={at} title={formatDateTime(at)} className="ml-auto shrink-0 pl-2 whitespace-nowrap text-fg-3">
      {chatTime(at)}
    </time>
  );
}

/** The people of the installation by name, for the pictures in the chat. */
export function usePeople(): Map<string, Person> {
  const directory = useDirectory().data;
  return new Map((directory ?? []).map((p) => [p.name.toLowerCase(), p]));
}

/** What people wrote in one message, each with its writer. Where several people work in a project
 * the text carries their names in brackets, for the agent: the chat shows name and picture instead.
 * Usually one part; several when messages of different people waited for the same turn.
 * `author`: who wrote it, if the message says so. Without a name it is an older message. */
export function writtenParts(text: string, author: string | null, people: Map<string, Person>): { author: string | null; text: string }[] {
  const known = (name: string) => people.has(name.toLowerCase()) || name.toLowerCase() === author?.toLowerCase();
  const parts: { author: string | null; text: string }[] = [];
  for (const line of text.split("\n")) {
    const named = /^\[([^\]\n]{1,100})\] (.*)$/s.exec(line);
    if (named && known(named[1])) parts.push({ author: named[1], text: named[2] });
    else if (parts.length) parts[parts.length - 1].text += `\n${line}`;
    else parts.push({ author, text: line });
  }
  return parts.map((p) => ({ ...p, text: p.text.trim() })).filter((p, i) => p.text || i === 0);
}

function UserMessage({ message, details }: { message: Message; details: boolean }) {
  const { t } = useTranslation();
  const { agentById, openTask } = useShell();
  const people = usePeople();
  const me = useMe();
  const trigger: string = message.meta.trigger ?? "chat";
  const inbox: string[] = message.meta.inbox ?? [];
  const source = agentById(message.meta.source_agent_id);

  const briefing: string[] = message.meta.briefing_update ?? [];
  const briefingNote = details && briefing.length > 0 && (
    <Note icon={<RefreshCw />} label={t("chat.briefingUpdated", { sections: briefing.map((k) => t(`chat.section.${k}`, k)).join(", ") })} at={message.created_at} />
  );
  const updates = inbox.length > 0 && (
    <Note
      icon={<Bell />}
      label={inbox.length === 1 && !details ? firstLine(inbox[0]) : t("chat.updates", { count: inbox.length })}
      at={message.created_at}
      chevron={details}
      defaultOpen={details && trigger === "wake"}
    >
      <ul className="list-disc space-y-1 pl-4">
        {inbox.map((item, i) => (
          <li key={i} className="break-words">
            {item}
          </li>
        ))}
      </ul>
    </Note>
  );

  if (trigger === "chat") {
    type Shown = { author: string | null; text: string; at: string; files: { id: number; filename: string; content_type: string }[] };
    const files: { id: number; filename: string; content_type: string }[] = message.meta.attachments ?? [];
    // Messages that waited for the same turn: each with its writer, time and files. Older ones say it in the text.
    const parts: Shown[] = message.meta.parts
      ? message.meta.parts.map((p: { author: string | null; text: string; attachment_ids: number[]; at: string }) => ({
          author: p.author,
          text: p.text,
          at: p.at,
          files: files.filter((f) => p.attachment_ids.includes(f.id)),
        }))
      : writtenParts(message.content, message.meta.author ?? null, people).map((p, i) => ({ ...p, at: message.created_at, files: i === 0 ? files : [] }));
    return (
      <>
        {briefingNote}
        {updates}
        {parts.map((part, i) => {
          // An older message names nobody: it looks like one's own, as it always did.
          const own = !part.author || part.author.toLowerCase() === me?.name.toLowerCase();
          return (
            <div key={i} data-part="msg" data-role="user" data-own={own ? "" : undefined} data-known={part.author ? "" : undefined} className={clsx(CHAT_ROW, "bg-sunk")}>
              <Head
                name={part.author ?? t("chat.you")}
                at={part.at}
                avatar={part.author ? <Avatar name={part.author} person={people.get(part.author.toLowerCase())} /> : undefined}
              />
              <div data-part="bubble">
                {part.files.length > 0 && <MessageFiles files={part.files} />}
                {part.text && <UserText text={part.text} />}
              </div>
            </div>
          );
        })}
      </>
    );
  }
  if (trigger === "wake")
    return (
      <>
        {briefingNote}
        {updates}
      </>
    );

  const cards: Record<string, { icon: ReactNode; title: string }> = {
    ticket: { icon: <Ticket />, title: t("chat.ticketHandoff") },
    assignment: { icon: <ClipboardList />, title: t("chat.assignmentFrom", { name: source?.name ?? t("chat.user") }) },
    ask: { icon: <HelpCircle />, title: t("chat.questionFrom", { name: source?.name ?? "?" }) },
    message: { icon: <Mail />, title: t("chat.messageFrom", { name: source?.name ?? "?" }) },
    schedule: { icon: <CalendarClock />, title: t("chat.scheduled") },
  };
  const card = cards[trigger] ?? { icon: <Mail />, title: trigger };
  return (
    <>
      {briefingNote}
      {updates}
      <Note
        icon={card.icon}
        label={card.title}
        at={message.created_at}
        chevron={details}
        action={
          message.meta.task_id ? (
            <button type="button" className={LINK} onClick={() => openTask(message.meta.task_id)}>
              {t("chat.openTicket")}
            </button>
          ) : undefined
        }
      >
        <Markdown>{message.content}</Markdown>
      </Note>
    </>
  );
}

function firstLine(text: string): string {
  const line = text.split(/\r?\n/)[0];
  return line.length > 110 ? `${line.slice(0, 109)}…` : line;
}

/** An event as one muted row: icon, label, time. Click to read what was sent. */
function Note({
  icon,
  label,
  at,
  children,
  action,
  chevron = false,
  defaultOpen = false,
}: {
  icon: ReactNode;
  label: string;
  at?: string;
  children?: ReactNode;
  action?: ReactNode;
  chevron?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div data-part="note" className="border-b border-line">
      <div className="flex items-center gap-2 px-4 py-2 text-fg-2">
        <button type="button" onClick={() => children && setOpen(!open)} aria-expanded={children ? open : undefined} className="flex min-w-0 flex-1 items-center gap-2 text-left hover:text-fg">
          {chevron && <ChevronRight className={clsx("size-4 shrink-0 text-fg-3 transition-transform", open && "rotate-90")} />}
          <span className="shrink-0 text-fg-3 [&>svg]:size-4">{icon}</span>
          <span className="truncate">{label}</span>
        </button>
        {action}
        {at && <MessageTime at={at} />}
      </div>
      {open && children && <div className="border-t border-line bg-sunk px-4 py-2.5 break-words text-fg-2">{children}</div>}
    </div>
  );
}

function MessageFiles({ files }: { files: { id: number; filename: string; content_type: string }[] }) {
  const [viewing, setViewing] = useState<number | null>(null);
  const images = files.filter(isMediaFile);
  const others = files.filter((f) => !isMediaFile(f));
  return (
    <div className="my-1 flex flex-wrap items-start gap-1.5">
      {images.map((f, i) => (
        <button key={f.id} type="button" onClick={() => setViewing(i)} className="overflow-hidden rounded-sm ring-1 ring-line">
          {isVideoFile(f) ? (
            <span className="block h-40 w-60 max-w-full">
              <VideoThumb src={`/api/attachments/${f.id}`} />
            </span>
          ) : (
            <img src={thumbSrc(f.id, 800)} alt={f.filename} loading="lazy" decoding="async" className="h-40 max-w-full object-cover" />
          )}
        </button>
      ))}
      {others.map((f) => (
        <AttachmentLink key={f.id} file={f} download className="flex max-w-full items-center gap-1.5 rounded-sm px-2 py-1 ring-1 ring-line-2 ring-inset hover:bg-sunk">
          <Paperclip className="size-4 shrink-0 text-fg-3" />
          <span className="truncate">{f.filename}</span>
        </AttachmentLink>
      ))}
      {viewing !== null && <Lightbox images={images.map((f) => ({ src: `/api/attachments/${f.id}`, alt: f.filename, video: isVideoFile(f) }))} start={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

const COLLAPSE_AT = 1200;

function UserText({ text }: { text: string }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const long = text.length > COLLAPSE_AT;
  return (
    <div className="break-words whitespace-pre-wrap">
      <TicketText text={long && !expanded ? `${text.slice(0, COLLAPSE_AT)}…` : text} linkClassName="underline decoration-line-2 underline-offset-[3px]" />
      {long && (
        <button type="button" onClick={() => setExpanded(!expanded)} className={clsx(LINK, "mt-1 block text-fg-2")}>
          {expanded ? t("chat.showLess") : t("chat.showMore", { count: text.length })}
        </button>
      )}
    </div>
  );
}

function toolLabel(name: string): string {
  return name.replace(/^mcp__plainboard__/, "").replace(/^mcp__([^_]+)__/, "$1 · ");
}

/** A tool step: the code line where the step carries code and the user wants it, else the plain line. */
function ToolStep({ message }: { message: Message }) {
  const [showCode] = useShowCode();
  return showCode && message.meta.code ? <CodeLine message={message} /> : <ToolLine message={message} />;
}

function ToolLine({ message }: { message: Message }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { name, input, result, is_error } = message.meta as { name?: string; input?: string; result?: string; is_error?: boolean };
  const pending = result === undefined;
  return (
    <div data-part="note" className="border-b border-line">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-2 px-4 py-1.5 text-left text-fg-3 hover:bg-sunk">
        <Wrench className={clsx("size-4 shrink-0", is_error ? "text-fg" : pending && "animate-pulse text-fg")} />
        <span className={clsx("shrink-0 font-mono text-[0.9em]", is_error ? "font-semibold text-fg line-through decoration-fg-3" : "text-fg-2")}>{toolLabel(name ?? message.content)}</span>
        {input && input !== "{}" && <span className="truncate">{input}</span>}
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-line bg-sunk px-4 py-2.5 font-mono text-[0.9em]">
          {input && <pre className="break-words whitespace-pre-wrap text-fg-2">{input}</pre>}
          {result !== undefined && <pre className={clsx("max-h-64 overflow-auto break-words whitespace-pre-wrap", is_error ? "text-fg-2" : "text-fg")}>{result || "(empty)"}</pre>}
          {message.meta.trimmed && <p className="font-sans text-fg-3">{t("chat.toolTrimmed", { days: message.meta.trimmed_days })}</p>}
        </div>
      )}
    </div>
  );
}

function SummaryDivider({ message }: { message: Message }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const label = message.meta.fresh ? "chat.freshContext" : message.meta.provider ? "chat.compactedByProvider" : "chat.compressed";
  return (
    <div className="rule-b">
      <button type="button" onClick={() => setOpen(!open)} disabled={!message.content} aria-expanded={open} className="flex w-full items-center gap-2 px-4 py-2 text-left text-fg-2 enabled:hover:text-fg">
        <Archive className="size-4 shrink-0 text-fg-3" />
        {t(label, { tokens: formatTokens(message.meta.tokens_before ?? 0) })}
      </button>
      {open && (
        <div className="border-t border-line bg-sunk px-4 py-2.5">
          <Markdown>{message.content}</Markdown>
        </div>
      )}
    </div>
  );
}
