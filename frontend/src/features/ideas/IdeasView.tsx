import clsx from "clsx";
import { Check, CornerDownLeft, Lightbulb, Loader2, MessageSquare, Paperclip, PencilRuler, RotateCcw, Send, Settings, Shuffle, TriangleAlert, Undo2, X } from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ClipboardEvent,
  type CSSProperties,
  type DragEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import { expectIdeaChange, showIdeaChange, showIdeaNote, upsertIdea, useIdeaChange, useIdeaNote } from "../../api/events";
import { qk, useIdeas } from "../../api/queries";
import type { Idea, IdeaChange, IdeasData, IdeaTopic } from "../../api/types";
import { PendingFile } from "../../components/PendingFile";
import { Button, IconButton, Input, PageHead, Spinner } from "../../components/ui";
import { sendsOnEnter, useIsDesktop } from "../../lib/hooks";
import { useDraft, useDraftFiles } from "../../lib/drafts";
import { useShell } from "../layout/shell-context";
import { IdeaCard, ideaTitle, ReadingLine } from "./IdeaCard";
import { IdeaDialog } from "./IdeaDialog";
import { HandOverBar } from "./HandOverBar";
import { Outbox, type Outgoing } from "./outbox";
import "./ideas.css";

const DRAG_TYPE = "application/x-plainboard-idea";
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The project's ideas as a pinboard (#91): the AI gathers them into soft coloured topic islands. A planning
 * space: collecting and sorting starts nothing; tickets come from the manager when asked. */
export function IdeasView({ focus }: { focus?: { id: number; n: number } }) {
  const { t } = useTranslation();
  const { project, canWrite, agents, openChat } = useShell();
  const pid = project.id;
  const manager = canWrite ? agents.find((a) => a.is_manager && a.enabled) : undefined;
  const [handed, setHanded] = useState(0);
  // The ideas collected for the manager; while there is a list, the bar takes the input's place. Kept like a draft.
  const [trayDraft, setTrayDraft, clearTray] = useDraft(`idea-handover-tray:${pid}`);
  const tray = useMemo<number[] | null>(() => {
    try {
      return trayDraft ? (JSON.parse(trayDraft) as number[]) : null;
    } catch {
      return null;
    }
  }, [trayDraft]);
  const setTray = (ids: number[]) => setTrayDraft(JSON.stringify(ids));
  useEffect(() => {
    if (!handed) return;
    const timer = window.setTimeout(() => setHanded(0), 8000);
    return () => window.clearTimeout(timer);
  }, [handed]);
  const qc = useQueryClient();
  const ideas = useIdeas(pid);
  const data = ideas.data;
  const isDesktop = useIsDesktop();
  const [openId, setOpenId] = useState<number | null>(null);
  const [text, setText] = useDraft(`idea:${pid}`);
  const [files, setFiles] = useDraftFiles(`idea:${pid}`);
  const outbox = useOutbox(pid, qc);
  const outgoing = useSyncExternalStore(outbox.subscribe, outbox.snapshot);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [multiline, setMultiline] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);

  const byId = useMemo(() => new Map((data?.ideas ?? []).map((i) => [i.id, i])), [data]);
  const open = (data?.ideas ?? []).filter((i) => i.status === "open");
  const notHanded = open.filter((i) => !i.handed_over_at);
  const reading = [...outgoing.filter((o) => o.state === "sending").map((o) => o.text), ...(data?.ideas ?? []).filter((i) => i.status === "reading").map((i) => i.text)];
  const shown = data?.ideas ?? [];
  const known = useRef<Set<number> | null>(null);
  if (data && known.current === null) known.current = new Set(data.ideas.map((i) => i.id));
  const entering = (id: number) => !!known.current && !known.current.has(id);
  useEffect(() => {
    if (data && known.current) data.ideas.forEach((i) => known.current!.add(i.id));
  }, [data]);

  // Taken, merged or left-out ideas show where they went for a moment, then leave.
  useEffect(() => {
    if (!data?.ideas.some(closed)) return;
    const timer = window.setTimeout(
      () => qc.setQueryData<IdeasData>(qk.ideas(pid), (old) => old && { ...old, ideas: old.ideas.filter((i) => !closed(i)) }),
      1800,
    );
    return () => window.clearTimeout(timer);
  }, [data, pid, qc]);

  useFlip(boardRef);

  const show = (id: number) => {
    const el = boardRef.current?.querySelector<HTMLElement>(`[data-idea="${id}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "center", inline: "center" });
    el.classList.remove("idea-flash");
    void el.offsetWidth;
    el.classList.add("idea-flash");
  };
  useEffect(() => {
    if (focus && data) window.setTimeout(() => show(focus.id), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.n, !!data]);

  const patchIdea = (id: number, change: Partial<Idea>) =>
    qc.setQueryData<IdeasData>(qk.ideas(pid), (old) => old && { ...old, ideas: old.ideas.map((i) => (i.id === id ? { ...i, ...change } : i)) });
  const move = async (id: number, topicId: number | null) => {
    const idea = byId.get(id);
    if (!idea || idea.topic_id === topicId) return;
    patchIdea(id, { topic_id: topicId, topic_manual: true });
    try {
      await api.patch(`/projects/${pid}/ideas/${id}`, { topic_id: topicId });
    } catch (err) {
      patchIdea(id, { topic_id: idea.topic_id, topic_manual: idea.topic_manual });
      setError(errorMessage(err));
    }
  };
  const rename = async (topic: IdeaTopic, name: string) => {
    if (!name || name === topic.name) return;
    try {
      await api.patch(`/projects/${pid}/ideas/topics/${topic.id}`, { name });
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  // The field is one line until the text no longer fits; then it grows with it up to a cap and scrolls.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const line = parseFloat(getComputedStyle(el).lineHeight) || 20;
    el.style.height = `${Math.min(el.scrollHeight, line * 8)}px`;
    el.style.overflowY = el.scrollHeight > el.clientHeight + 1 ? "auto" : "hidden";
    setMultiline(el.scrollHeight > line * 1.5);
  }, [text]);
  // The field is free again at once (#161): the input is queued and shown until the server has it; the AI reads
  // it in the background. Pasted or dropped files go with it: onto the idea it results in (new, added to or
  // merged into). Files alone become a new idea named after them.
  const submit = () => {
    const value = text.trim();
    if (!value && !files.length) return;
    void outbox.add(value, files);
    setText("");
    setFiles([]);
    setError("");
  };
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    // Office apps also put a picture of copied text on the clipboard: text wins.
    const pasted = Array.from(e.clipboardData.files);
    const pastedText = e.clipboardData.getData("text/plain");
    if (pasted.length && !pastedText) {
      e.preventDefault();
      setFiles((cur) => [...cur, ...pasted]);
    }
    // The field takes IDEA_TEXT_MAX characters and cuts the rest of a paste: say so.
    const field = e.currentTarget;
    if (text.length - (field.selectionEnd - field.selectionStart) + pastedText.length > IDEA_TEXT_MAX) {
      setError(t("ideas.tooLong", { max: IDEA_TEXT_MAX.toLocaleString() }));
    }
  };
  const hasFiles = (e: DragEvent) => e.dataTransfer.types.includes("Files");
  const rearrange = async () => {
    try {
      showIdeaNote(null);
      qc.setQueryData<IdeasData>(qk.ideas(pid), (old) => old && { ...old, sorting: true });
      await api.post(`/projects/${pid}/ideas/sort`);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  if (ideas.isLoading || !data) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="size-6" />
      </div>
    );
  }

  const topics = [...data.topics].sort((a, b) => a.id - b.id);
  const topicIds = new Set(topics.map((tp) => tp.id));
  const islands = topics.map((topic) => ({ topic, ideas: shown.filter((i) => i.topic_id === topic.id) })).filter((x) => x.ideas.length);
  const loose = shown.filter((i) => i.topic_id === null || !topicIds.has(i.topic_id));
  const empty = shown.length === 0 && outgoing.length === 0;
  const opened = openId !== null ? byId.get(openId) : undefined;
  const drag = canWrite && isDesktop;

  return (
    <div
      data-part="ideas"
      className="flex h-full flex-col"
      onDragOver={canWrite ? (e) => hasFiles(e) && e.preventDefault() : undefined}
      onDrop={
        canWrite
          ? (e) => {
              if (!hasFiles(e)) return;
              e.preventDefault();
              setFiles((cur) => [...cur, ...Array.from(e.dataTransfer.files)]);
              inputRef.current?.focus();
            }
          : undefined
      }
    >
      <PageHead title={t("nav.ideas")}>
        <span
          title={t("ideas.planningOnlyLong")}
          tabIndex={0}
          data-part="idea-planning"
          className="inline-flex cursor-help items-center gap-1.5 rounded-sm px-2 py-0.5 whitespace-nowrap text-fg-2 ring-1 ring-line-2 ring-inset"
        >
          <PencilRuler className="size-3.5" />
          {t("ideas.planningOnly")}
        </span>
        {!empty && (
          <span className="ml-1 whitespace-nowrap text-fg-3 max-md:hidden">
            {t("ideas.count", { count: open.length })} · {t("ideas.topics", { count: islands.length })}
          </span>
        )}
        {canWrite && open.length > 1 && (
          <Button variant="ghost" icon={data.sorting ? <Loader2 className="size-4 animate-spin" /> : <Shuffle className="size-4" />} disabled={data.sorting} onClick={rearrange} aria-label={t("ideas.rearrange")}>
            <span className="max-sm:hidden">{t("ideas.rearrange")}</span>
          </Button>
        )}
        {manager && notHanded.length > 0 && (
          <Button
            data-part="ideas-handover-all"
            icon={<Send className="size-4" />}
            variant="ghost"
            onClick={() => setTray(notHanded.map((i) => i.id))}
            aria-label={t("ideas.handOver.all", { name: manager.name })}
          >
            <span className="max-sm:hidden">{t("ideas.handOver.all", { name: manager.name })}</span>
          </Button>
        )}
      </PageHead>
      <div className="relative min-h-0 flex-1">
        <div ref={boardRef} className="idea-dots scroll-thin h-full overflow-auto">
          {empty ? (
            <Empty
              canWrite={canWrite}
              onStarter={(s) => {
                setText(s);
                inputRef.current?.focus();
              }}
            />
          ) : (
            <div className={clsx("flex flex-wrap items-start justify-center gap-8 px-4 pt-6 sm:px-6 sm:pt-10", canWrite ? "pb-44" : "pb-16")}>
              {(loose.length > 0 || outgoing.length > 0) && (
                <Island
                  name={loose.some((i) => i.sort_state === "failed") ? t("ideas.notSorted") : t("ideas.unsorted")}
                  hint={loose.some((i) => i.sort_state === "pending") || outgoing.length ? t("ideas.unsortedHint") : ""}
                  color={null}
                  ideas={loose}
                  onDrop={drag ? (id) => move(id, null) : undefined}
                  card={(idea) => card(idea)}
                  extra={outgoing.map((item) => (
                    <OutgoingCard key={`out-${item.key}`} item={item} onRetry={() => void outbox.retry(item.key)} onRemove={() => outbox.remove(item.key)} />
                  ))}
                />
              )}
              {islands.map(({ topic, ideas: list }, n) => (
                <Island
                  key={topic.id}
                  name={topic.name}
                  hint={topic.hint}
                  color={topic.color}
                  ideas={list}
                  offset={n % 2 === 1}
                  onRename={canWrite ? (name) => rename(topic, name) : undefined}
                  onDrop={drag ? (id) => move(id, topic.id) : undefined}
                  card={(idea) => card(idea)}
                />
              ))}
              <ManagerHint className="basis-full" />
            </div>
          )}
        </div>
        {canWrite && (
          <>
            <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-bg to-transparent" />
            <div className="pointer-events-none absolute inset-x-0 bottom-3 flex flex-col items-center gap-2 px-3 sm:bottom-5">
              {data.model_blocked && <ModelBlocked blocked={data.model_blocked} />}
              <Notice error={error} onDismissError={() => setError("")} byId={byId} />
              {handed > 0 && manager && (
                <div role="status" data-part="idea-handover-sent" className="pointer-events-auto flex max-w-full items-center gap-2 rounded-sm bg-bg py-1 pr-1 pl-3 shadow-lg ring-1 ring-line-2">
                  <Check className="size-4 shrink-0" />
                  <span className="min-w-0 font-medium">{t("ideas.handOver.sent", { name: manager.name, count: handed })}</span>
                  <Button size="sm" variant="ghost" icon={<MessageSquare className="size-3.5" />} onClick={() => openChat(manager.id)}>
                    {t("ideas.handOver.openChat")}
                  </Button>
                  <IconButton label={t("common.close")} onClick={() => setHanded(0)}>
                    <X className="size-3.5" />
                  </IconButton>
                </div>
              )}
              {(reading.length > 0 || data.sorting) && (
                <div role="status" className="idea-sorting pointer-events-auto flex max-w-full items-center gap-2 rounded-full bg-bg px-3 py-1 text-fg-2 shadow-lg ring-1 ring-line">
                  <Loader2 className="size-3.5 shrink-0 animate-spin" />
                  {reading.length === 1 && <span className="min-w-0 truncate">„{reading[0]}“</span>}
                  <span className="shrink-0 text-fg-3">
                    {reading.length === 1 ? t("ideas.readingInput") : reading.length > 1 ? t("ideas.readingInputs", { count: reading.length }) : t("ideas.sorting")}
                  </span>
                </div>
              )}
              {tray && manager ? (
                <HandOverBar
                  manager={manager}
                  ideas={tray.map((id) => byId.get(id)).filter((i): i is Idea => !!i && i.status === "open" && !i.handed_over_at)}
                  onRemove={(id) => setTray(tray.filter((x) => x !== id))}
                  onClose={clearTray}
                  onSent={(count) => {
                    setHanded(count);
                    clearTray();
                  }}
                />
              ) : (
              <form
                data-part="idea-composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  submit();
                }}
                className={clsx(
                  "pointer-events-auto w-full max-w-2xl bg-[var(--card,var(--bg))] shadow-lg ring-1 ring-line-2",
                  multiline || files.length ? "rounded-2xl" : "rounded-full",
                )}
              >
                {files.length > 0 && (
                  <div data-part="idea-files-pending" className="flex flex-wrap items-end gap-2 px-3 pt-3">
                    {files.map((file, i) => (
                      <PendingFile key={`${file.name}-${file.lastModified}-${i}`} file={file} onRemove={() => setFiles((cur) => cur.filter((_, j) => j !== i))} />
                    ))}
                  </div>
                )}
                <div className={clsx("flex gap-2 py-1.5 pr-1.5 pl-4", multiline ? "items-end" : "items-center")}>
                  <Lightbulb
                    className={clsx("size-4 shrink-0 text-fg-3", multiline && "mt-[calc(0.25rem_+_(1lh_-_1rem)_/_2)] self-start")}
                  />
                  <textarea
                    ref={inputRef}
                    rows={1}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onPaste={onPaste}
                    onKeyDown={(e) => {
                      if (!sendsOnEnter(e)) return;
                      e.preventDefault();
                      submit();
                    }}
                    maxLength={IDEA_TEXT_MAX}
                    aria-label={t("ideas.placeholder")}
                    placeholder={empty ? t("ideas.placeholderFirst") : t("ideas.placeholder")}
                    className={clsx(
                      "scroll-thin min-w-0 flex-1 resize-none overflow-y-hidden bg-transparent text-fg placeholder:truncate placeholder:text-fg-3 focus:outline-none",
                      multiline && "my-1",
                    )}
                  />
                  <span className={clsx("inline-flex items-center gap-1 text-fg-3 touch:hidden", multiline && "h-8")}>
                    <CornerDownLeft className="size-3.5" />
                  </span>
                  <input
                    ref={picker}
                    type="file"
                    multiple
                    hidden
                    onChange={(e) => {
                      const picked = Array.from(e.target.files ?? []);
                      setFiles((cur) => [...cur, ...picked]);
                      e.target.value = "";
                    }}
                  />
                  <IconButton label={t("ideas.attach")} onClick={() => picker.current?.click()} className="rounded-full! size-8 touch:size-11">
                    <Paperclip className="size-4" />
                  </IconButton>
                  <Button type="submit" variant="primary" className="rounded-full!" disabled={!text.trim() && !files.length}>
                    {t("ideas.add")}
                  </Button>
                </div>
              </form>
              )}
            </div>
          </>
        )}
      </div>
      {opened && <IdeaDialog key={opened.id} idea={opened} topics={topics} canWrite={canWrite} onClose={() => setOpenId(null)} />}
    </div>
  );

  function card(idea: Idea) {
    return (
      <IdeaCard
        key={idea.id}
        idea={idea}
        byId={byId}
        canWrite={canWrite}
        draggable={drag}
        entering={entering(idea.id)}
        onOpen={() => setOpenId(idea.id)}
        onShow={show}
        manager={manager?.name}
        selected={!!tray?.includes(idea.id)}
        onHandOver={() => setTray(tray?.includes(idea.id) ? tray.filter((x) => x !== idea.id) : [...(tray ?? []), idea.id])}
      />
    );
  }
}

/** The longest text of an idea, as the server takes it (schemas.IDEA_TEXT_MAX). */
const IDEA_TEXT_MAX = 100_000;
const fileName = (file: File) => file.name || "pasted.png";
const closed = (idea: Idea) => idea.status !== "open" && idea.status !== "reading";

// Per project, outside the view: leaving the page loses nothing still being sent.
const outboxes = new Map<number, Outbox<File>>();

function useOutbox(pid: number, qc: QueryClient): Outbox<File> {
  let box = outboxes.get(pid);
  if (!box) {
    box = new Outbox<File>(async (item) => {
      const form = new FormData();
      form.append("text", item.text);
      for (const file of item.files) form.append("files", file, fileName(file));
      const idea = await api.post<Idea>(`/projects/${pid}/ideas/input`, form);
      if (idea.status === "reading") expectIdeaChange(idea.id);
      upsertIdea(qc, pid, idea);
    }, errorMessage);
    outboxes.set(pid, box);
  }
  return box;
}

/** An input on its way to the server, or one that did not get there: with "try again". */
function OutgoingCard({ item, onRetry, onRemove }: { item: Outgoing<File>; onRetry: () => void; onRemove: () => void }) {
  const { t } = useTranslation();
  const failed = item.state === "failed";
  const title = item.text || item.files.map(fileName).join(", ");
  return (
    <div data-part="idea-outgoing" className="relative idea-enter">
      <div
        className={clsx(
          "flex flex-col gap-1.5 rounded-md bg-[var(--card,var(--bg))] px-3 py-2.5 shadow-[var(--shadow-card,0_0_#0000)] ring-1",
          failed ? "ring-you-fg/60" : "ring-line",
        )}
      >
        <div className="leading-snug font-medium whitespace-pre-line text-fg">{title.length > 200 ? `${title.slice(0, 200)}…` : title}</div>
        {item.files.length > 0 && (
          <span title={t("ideas.files", { count: item.files.length })} className="inline-flex items-center gap-1 self-start text-[0.92em] text-fg-3">
            <Paperclip aria-hidden className="size-3.5" />
            <span className="sr-only">{t("ideas.files", { count: item.files.length })}</span>
            <span aria-hidden>{item.files.length}</span>
          </span>
        )}
        {failed ? (
          <div role="alert" className="flex flex-col gap-1">
            <span className="text-[0.92em] text-you-fg">{t("ideas.sendFailed", { error: item.error })}</span>
            <div className="-mx-2 flex items-center justify-between gap-1">
              <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={onRetry}>
                {t("ideas.retry")}
              </Button>
              <IconButton label={t("ideas.discard")} onClick={onRemove}>
                <X className="size-3.5" />
              </IconButton>
            </div>
          </div>
        ) : (
          <ReadingLine />
        )}
      </div>
    </div>
  );
}

/** A topic: a soft outline in its colour around its cards; ideas dropped on it are pinned there. */
function Island({
  name,
  hint,
  color,
  ideas,
  offset,
  onRename,
  onDrop,
  card,
  extra,
}: {
  name: string;
  hint: string;
  color: number | null;
  ideas: Idea[];
  offset?: boolean;
  onRename?: (name: string) => void;
  onDrop?: (id: number) => void;
  card: (idea: Idea) => React.ReactNode;
  /** Inputs not on the server yet, after its cards. */
  extra?: ReactNode[];
}) {
  const { t } = useTranslation();
  const [over, setOver] = useState(false);
  const [editing, setEditing] = useState(false);
  const count = ideas.length + (extra?.length ?? 0);
  const wide = count >= 5;
  const style = { "--c": color === null ? "var(--fg-3)" : `var(--chart-${(color % 10) + 1})` } as CSSProperties;
  return (
    <section
      data-part="idea-topic"
      style={style}
      aria-label={name}
      onDragOver={
        onDrop &&
        ((e) => {
          if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          setOver(true);
        })
      }
      onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setOver(false)}
      onDrop={
        onDrop &&
        ((e) => {
          e.preventDefault();
          setOver(false);
          const id = Number(e.dataTransfer.getData(DRAG_TYPE));
          if (id) onDrop(id);
        })
      }
      className={clsx(
        "idea-island relative isolate w-full px-5 py-5 sm:px-8 sm:py-7",
        wide ? "sm:w-[520px]" : "sm:w-[300px]",
        offset && "sm:mt-10",
        color === null && "idea-island-loose",
        over && "idea-island-over",
      )}
    >
      <header className="mb-1 flex min-h-7 items-center gap-2">
        <span className="size-2.5 shrink-0 rounded-full bg-[var(--c)]" />
        {editing && onRename ? (
          <Input
            autoFocus
            defaultValue={name}
            maxLength={100}
            aria-label={t("ideas.renameTopic")}
            className="h-7! font-semibold"
            onBlur={(e) => {
              setEditing(false);
              onRename(e.target.value.trim());
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : onRename ? (
          <button type="button" title={t("ideas.renameTopic")} onClick={() => setEditing(true)} className="min-w-0 truncate text-left font-semibold hover:underline">
            {name}
          </button>
        ) : (
          <h2 className="min-w-0 truncate font-semibold">{name}</h2>
        )}
        <span className="text-fg-3 tabular-nums">{ideas.filter((i) => !closed(i)).length}</span>
      </header>
      {hint && <p className="mb-3 text-[0.92em] text-fg-2">{hint}</p>}
      <div
        className={clsx(
          "grid gap-2.5 max-sm:-mx-5 max-sm:flex max-sm:snap-x max-sm:overflow-x-auto max-sm:px-5 max-sm:pt-1 max-sm:pb-2 max-sm:[&>*]:w-[78%] max-sm:[&>*]:shrink-0 max-sm:[&>*]:snap-center",
          wide && "sm:grid-cols-2",
          count === 1 && "max-sm:[&>*]:w-full",
        )}
      >
        {ideas.map(card)}
        {extra}
      </div>
    </section>
  );
}

/** The idea model set can't be used in this project: no other one sorts instead, so ideas wait unsorted. */
function ModelBlocked({ blocked }: { blocked: NonNullable<IdeasData["model_blocked"]> }) {
  const { t } = useTranslation();
  const { isOwner, openProjectSettings } = useShell();
  return (
    <div role="alert" data-part="idea-model-blocked" className="pointer-events-auto flex w-full max-w-2xl flex-wrap items-center justify-end gap-x-2 gap-y-1 rounded-sm bg-bg py-1.5 pr-1 pl-3 shadow-lg ring-1 ring-line-2">
      <span className="flex min-w-0 flex-1 basis-64 items-start gap-2 text-you-fg">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" />
        <span className="min-w-0">
        {t("ideas.modelBlocked", { provider: blocked.provider_label, model: blocked.model })}
        {!isOwner && ` ${t("ideas.modelBlockedAskOwner")}`}
        </span>
      </span>
      {isOwner && (
        <Button size="sm" variant="ghost" icon={<Settings className="size-3.5" />} onClick={() => openProjectSettings("team")}>
          {t("ideas.modelBlockedChange")}
        </Button>
      )}
    </div>
  );
}

/** What the AI just did with the input, or how it rearranged the topics, with an undo. */
function Notice({ error, onDismissError, byId }: { error: string; onDismissError: () => void; byId: Map<number, Idea> }) {
  const { t } = useTranslation();
  const change = useIdeaChange();
  const note = useIdeaNote();
  const { project } = useShell();
  const [busy, setBusy] = useState(false);
  const [undoError, setUndoError] = useState("");
  useEffect(() => {
    setUndoError("");
    if (!change) return;
    const timer = window.setTimeout(() => showIdeaChange(null), 12_000);
    return () => window.clearTimeout(timer);
  }, [change]);
  useEffect(() => {
    if (!note || note.error) return;
    const timer = window.setTimeout(() => showIdeaNote(null), 8_000);
    return () => window.clearTimeout(timer);
  }, [note]);
  const shownNote = note?.project_id === project.id ? note : null;
  if (error || shownNote) {
    const failed = error || (shownNote?.error ? t("ideas.sortFailed", { error: shownNote.error }) : "");
    return (
      <div role={failed ? "alert" : "status"} className="pointer-events-auto flex max-w-full items-center gap-2 rounded-sm bg-bg py-1 pr-1 pl-3 shadow-lg ring-1 ring-line-2">
        <span className={clsx("min-w-0", failed ? "text-you-fg" : "font-medium")}>{failed || t("ideas.notice.unchanged")}</span>
        <IconButton label={t("common.close")} onClick={error ? onDismissError : () => showIdeaNote(null)}>
          <X className="size-3.5" />
        </IconButton>
      </div>
    );
  }
  if (!change || change.project_id !== project.id) return null;
  const undo = async () => {
    setBusy(true);
    try {
      await api.post(`/projects/${project.id}/ideas/changes/${change.id}/undo`);
      showIdeaChange(null);
    } catch (err) {
      setUndoError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div role="status" data-part="idea-notice" className="idea-notice pointer-events-auto flex max-w-full items-center gap-2 rounded-sm bg-bg py-1 pr-1 pl-3 shadow-lg ring-1 ring-line-2">
      <span className="min-w-0">
        <span className="font-medium">{noticeText(change, byId, t)}</span>
        {undoError && <span className="text-you-fg"> · {undoError}</span>}
      </span>
      <Button size="sm" variant="ghost" icon={<Undo2 className="size-3.5" />} loading={busy} onClick={undo}>
        {t("ideas.undo")}
      </Button>
      <IconButton label={t("common.close")} onClick={() => showIdeaChange(null)}>
        <X className="size-3.5" />
      </IconButton>
    </div>
  );
}

function noticeText(change: IdeaChange, board: Map<number, Idea>, t: (key: string, o?: Record<string, unknown>) => string): string {
  const s = change.summary;
  // A change that came as an event holds no ideas: those on the board stand in.
  const byId = new Map([...board, ...change.ideas.map((i) => [i.id, i] as const)]);
  const target = byId.get(s.idea_id ?? s.created?.[0]);
  const title = s.after?.title || (target ? ideaTitle(target) : "");
  if (change.kind === "reorder") {
    const parts = [t("ideas.notice.reorder")];
    if (s.moved?.length) parts.push(t("ideas.notice.moved", { count: s.moved.length }));
    if (s.created?.length) parts.push(t("ideas.notice.newTopics", { count: s.created.length }));
    if (s.renamed?.length) parts.push(t("ideas.notice.renamed", { count: s.renamed.length }));
    return parts.join(" · ");
  }
  if (s.fallback && change.kind === "new") return t("ideas.notice.kept");
  if (change.kind === "amend") return t("ideas.notice.amend", { title });
  if (change.kind === "merge" && s.duplicates) return t("ideas.notice.duplicates", { title });
  if (change.kind === "merge") return t("ideas.notice.merge", { title, count: (s.merged?.length ?? 0) + 1 });
  if (change.kind === "several") return t("ideas.notice.several", { count: s.created?.length ?? 2 });
  if (change.kind === "split") return t("ideas.notice.split", { title: s.before?.title || title, count: (s.created?.length ?? 1) + 1 });
  if (change.kind === "resolve") return s.dropped?.length ? t("ideas.notice.dropped", { count: s.dropped.length }) : t("ideas.notice.resolved");
  if (change.kind === "question") return t("ideas.notice.question");
  if (s.similar?.length) return t("ideas.notice.newSimilar", { title });
  return t("ideas.notice.new", { title });
}

/** Tickets come from the manager when asked: a quiet hint where to ask. */
function ManagerHint({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { agents, openChat, canWrite } = useShell();
  const manager = agents.find((a) => a.is_manager);
  return (
    <p data-part="idea-manager-hint" className={clsx("flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-center text-fg-3", className)}>
      <span>{t("ideas.managerHint")}</span>
      {manager && canWrite && (
        <Button size="sm" variant="ghost" icon={<MessageSquare className="size-3.5" />} onClick={() => openChat(manager.id)}>
          {t("ideas.writeManager")}
        </Button>
      )}
    </p>
  );
}

const STARTERS = ["who", "must", "special", "annoys"] as const;

/** A new project: what this place is for, and questions to start from. */
function Empty({ canWrite, onStarter }: { canWrite: boolean; onStarter: (text: string) => void }) {
  const { t } = useTranslation();
  return (
    <div data-part="ideas-empty" className="relative flex min-h-full flex-col items-center justify-center px-6 pt-8 pb-44">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden max-sm:hidden">
        <div className="idea-ghost-island absolute top-[12%] left-[10%] h-44 w-72" />
        <div className="idea-ghost-island absolute top-[18%] right-[9%] h-52 w-80" />
        <div className="idea-ghost-island absolute bottom-[22%] left-[22%] h-40 w-64" />
      </div>
      <div className="relative flex max-w-md flex-col items-center gap-3 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-sunk text-fg-2">
          <Lightbulb className="size-6" />
        </span>
        <h2 className="font-extrabold">{t("ideas.empty.title")}</h2>
        <p className="text-fg-2">{t("ideas.empty.text")}</p>
        {canWrite && (
          <>
            <div className="mt-2 text-fg-3">{t("ideas.empty.starters")}</div>
            <div className="flex flex-wrap justify-center gap-2">
              {STARTERS.map((s) => (
                <button key={s} type="button" onClick={() => onStarter(t(`ideas.starter.${s}`))} className="rounded-full px-3 py-1 text-fg-2 ring-1 ring-line-2 hover:bg-sunk hover:text-fg touch:min-h-11">
                  {t(`ideas.starter.${s}`)}
                </button>
              ))}
            </div>
          </>
        )}
        <ManagerHint className="mt-3" />
      </div>
    </div>
  );
}

/** Cards that changed place glide there from where they were (FLIP); not with reduced motion. */
function useFlip(ref: RefObject<HTMLElement | null>) {
  const last = useRef(new Map<string, { x: number; y: number }>());
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const base = root.getBoundingClientRect();
    const next = new Map<string, { x: number; y: number }>();
    const still = reducedMotion();
    root.querySelectorAll<HTMLElement>("[data-idea]").forEach((el) => {
      const id = el.dataset.idea!;
      el.getAnimations().forEach((a) => a.id === "flip" && a.cancel());
      // Where the card lies in its content, not on screen: scrolling (also a topic's row on the phone) is no move.
      const r = el.getBoundingClientRect();
      const pos = { x: r.left - base.left, y: r.top - base.top };
      for (let p = el.parentElement; p; p = p === root ? null : p.parentElement) {
        pos.x += p.scrollLeft;
        pos.y += p.scrollTop;
      }
      next.set(id, pos);
      const prev = last.current.get(id);
      if (!prev || still) return;
      const dx = prev.x - pos.x;
      const dy = prev.y - pos.y;
      if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return;
      const animation = el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 450, easing: "cubic-bezier(0.2, 0.8, 0.3, 1)" });
      animation.id = "flip";
    });
    last.current = next;
  });
}
