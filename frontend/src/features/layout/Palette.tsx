// The slim navigation (#94): a line of the sidebar, the "…" menu at the project name with the rare views,
// and the command palette (Ctrl+K) over areas, tickets, projects and the project's history by meaning.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  CornerDownLeft,
  ExternalLink,
  FileText,
  FolderKanban,
  GitCommitHorizontal,
  HelpCircle,
  BookOpen,
  Lightbulb,
  MessageSquareText,
  MoreHorizontal,
  NotebookPen,
  Paperclip,
  Palette,
  Search,
  Settings,
  Ticket,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { useTasks } from "../../api/queries";
import type { Project, ProjectSearch, SearchHit } from "../../api/types";
import { IconButton, Spinner } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useDebounced, useDismiss, useInView } from "../../lib/hooks";

export interface NavEntry {
  id: string;
  label: string;
  icon: (cls: string) => ReactNode;
  /** Something runs behind it (the browser while previews run): a dot on its icon in the rail, which has no list of it. */
  marked?: boolean;
}

/** One line of the sidebar: icon and label, or the icon alone in the rail. */
export function NavButton({ entry, active, wide, onClick }: { entry: NavEntry; active: boolean; wide: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={wide ? undefined : entry.label}
      aria-label={entry.label}
      aria-current={active ? "page" : undefined}
      className={clsx(
        "relative flex h-8 items-center gap-2.5 rounded-sm transition-colors",
        wide ? "px-2" : "justify-center",
        active ? "plate" : "text-fg-2 hover:bg-sunk hover:text-fg",
      )}
    >
      {entry.icon("size-4 shrink-0")}
      {wide && <span className="label-line">{entry.label}</span>}
      {entry.marked && !wide && <span className="mk mk-run absolute top-1 right-1 scale-75 bg-current" />}
    </button>
  );
}

/** The "…" at the project name: the rare views (the search has its own line). Marked where nothing else shows
 * that something runs behind one. Settings and design only on phones: the desktop sidebar has their icons at its foot. */
export function ProjectMenu({
  entries,
  marked = false,
  onSelect,
  onSettings,
  onLook,
  align = "left",
}: {
  entries: NavEntry[];
  marked?: boolean;
  onSelect: (id: string) => void;
  onSettings?: () => void;
  onLook?: () => void;
  align?: "left" | "right";
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const ref = useRef<HTMLDivElement>(null);
  const [menuRef, inView] = useInView<HTMLDivElement>(open);
  useDismiss(ref, open, () => setOpen(false));
  useEffect(() => {
    if (open) ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]')[active]?.focus();
  }, [open, active]);
  const items: { key: string; label: string; icon: ReactNode; kbd?: string; run: () => void }[] = [
    ...entries.map((e) => ({ key: e.id, label: e.label, icon: e.icon("size-4 shrink-0 text-fg-2"), run: () => onSelect(e.id) })),
    ...(onSettings ? [{ key: "settings", label: t("nav.settings"), icon: <Settings className="size-4 shrink-0 text-fg-2" />, run: onSettings }] : []),
    ...(onLook ? [{ key: "theme", label: t("nav.theme"), icon: <Palette className="size-4 shrink-0 text-fg-2" />, run: onLook }] : []),
  ];
  const onKey = (e: React.KeyboardEvent) => {
    if (!open) return;
    if (e.key === "ArrowDown") setActive((a) => (a + 1) % items.length);
    else if (e.key === "ArrowUp") setActive((a) => (a <= 0 ? items.length - 1 : a - 1));
    else if (e.key === "Escape") setOpen(false);
    else return;
    e.preventDefault();
  };
  const item = "flex w-full items-center gap-2.5 rounded-sm px-2 py-1.5 text-left outline-none hover:bg-sunk focus-visible:bg-sunk touch:py-3";
  return (
    <div ref={ref} className="relative" onKeyDown={onKey}>
      <IconButton
        label={t("palette.menu")}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setActive(-1);
          setOpen(!open);
        }}
        className={clsx("relative", open && "bg-sunk text-fg")}
      >
        <MoreHorizontal className="size-4" />
        {marked && <span className="mk mk-run absolute top-0.5 right-0.5 scale-75 bg-current" />}
      </IconButton>
      {open && (
        <div
          ref={menuRef}
          role="menu"
          data-part="navmenu"
          style={inView}
          className={clsx("absolute top-full z-40 mt-1 flex w-60 max-w-[calc(100vw-1rem)] flex-col rounded-sm bg-bg p-1 shadow-lg ring-1 ring-line-2", align === "left" ? "left-0" : "right-0")}
        >
          {items.map((it, i) => (
            <div key={it.key}>
              {i === entries.length && <div className="my-1 border-t border-line" />}
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                className={item}
                onMouseMove={() => active !== i && setActive(i)}
                onClick={() => {
                  setOpen(false);
                  it.run();
                }}
              >
                {it.icon}
                <span className="flex-1">{it.label}</span>
                {it.kbd && <kbd className="kbd">{it.kbd}</kbd>}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The sidebar's search line that opens the palette. */
export function PaletteButton({ wide, onClick }: { wide: boolean; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t("palette.open")}
      title={wide ? undefined : t("palette.open")}
      className={clsx("flex h-8 items-center gap-2 rounded-sm text-fg-3 hover:bg-sunk hover:text-fg", wide ? "w-full bg-sunk px-2" : "mx-auto size-8 justify-center")}
    >
      <Search className="size-4 shrink-0" />
      {wide && (
        <>
          <span className="flex-1 text-left">{t("palette.short")}</span>
          <kbd className="kbd">Ctrl K</kbd>
        </>
      )}
    </button>
  );
}

const KIND_ICON: Record<string, (cls: string) => ReactNode> = {
  ticket: (c) => <Ticket className={c} />,
  report: (c) => <FileText className={c} />,
  decision: (c) => <HelpCircle className={c} />,
  comment: (c) => <MessageSquareText className={c} />,
  note: (c) => <NotebookPen className={c} />,
  commit: (c) => <GitCommitHorizontal className={c} />,
  idea: (c) => <Lightbulb className={c} />,
  doc: (c) => <BookOpen className={c} />,
  file: (c) => <Paperclip className={c} />,
};

interface Item {
  key: string;
  group: "areas" | "tickets" | "history" | "projects";
  label: string;
  /** A ticket's number, a commit's short hash. */
  ref?: string;
  /** What kind of history it is, its excerpt and its age. */
  kind?: string;
  snippet?: string;
  age?: string;
  external?: boolean;
  icon: ReactNode;
  run?: () => void;
}

/** How long the palette (and the board's filter) waits after the last key before it asks the search by meaning. */
export const DEBOUNCE_MS = 250;
export const MIN_SEMANTIC = 3;

/** One search over the areas, the tickets of this project, its history by meaning and the projects. Titles
 * match at once; the history follows a moment after the last key. Off or not ready: a quiet line, no error. */
export function CommandPalette({
  open,
  onClose,
  areas,
  projects,
  current,
  onSelectProject,
  onOpenTask,
  onOpenNote,
  onOpenIdea,
  onOpenDocPage,
}: {
  open: boolean;
  onClose: () => void;
  areas: (NavEntry & { run: () => void })[];
  projects: Project[];
  current: Project;
  onSelectProject: (id: number) => void;
  onOpenTask: (id: number) => void;
  onOpenNote: (id: number) => void;
  onOpenIdea: (id: number) => void;
  onOpenDocPage: (id: number) => void;
}) {
  const { t } = useTranslation();
  const tasks = useTasks(current.id);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const q = query.trim();
  const debounced = useDebounced(q, DEBOUNCE_MS);
  const semantic = useQuery({
    queryKey: ["search", current.id, debounced],
    queryFn: () => api.get<ProjectSearch>(`/projects/${current.id}/search?q=${encodeURIComponent(debounced)}&limit=8`),
    enabled: open && debounced.length >= MIN_SEMANTIC,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  const hitItem = (h: SearchHit, i: number): Item => {
    const ticket = h.task_id;
    const run =
      ticket !== null
        ? () => onOpenTask(ticket)
        : h.note_id !== null
          ? () => onOpenNote(h.note_id!)
          : h.idea_id != null
            ? () => onOpenIdea(h.idea_id!)
            : h.doc_page_id != null
            ? () => onOpenDocPage(h.doc_page_id!)
            : h.url
            ? () => window.open(h.url!, "_blank", "noopener")
            : undefined;
    return {
      key: `h-${i}-${h.ref}`,
      group: "history",
      label: h.kind === "file" && h.filename ? `${h.filename} · ${h.title}` : h.title,
      ref: h.kind === "commit" ? h.sha?.slice(0, 7) : h.ticket_number !== null ? `#${h.ticket_number}` : undefined,
      kind: t(`palette.kinds.${h.kind}`, { defaultValue: h.kind }),
      snippet: h.snippet,
      age: timeAgo(h.changed_at),
      external: h.kind === "commit" && !!h.url,
      icon: (KIND_ICON[h.kind] ?? KIND_ICON.ticket)("size-4"),
      run,
    };
  };

  const fresh = semantic.data && debounced === q && q.length >= MIN_SEMANTIC;
  const items = useMemo<Item[]>(() => {
    const lower = q.toLowerCase();
    const hit = (s: string) => !lower || s.toLowerCase().includes(lower);
    const out: Item[] = areas.filter((a) => hit(a.label)).map((a) => ({ key: `a-${a.id}`, group: "areas", label: a.label, icon: a.icon("size-4"), run: a.run }));
    const tickets = (tasks.data ?? [])
      .filter((x) => !x.archived_at && (lower ? hit(x.title) || `#${x.number}`.startsWith(lower) || String(x.number) === lower : true))
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .slice(0, lower ? 6 : 4);
    for (const x of tickets) out.push({ key: `t-${x.id}`, group: "tickets", label: x.title, ref: `#${x.number}`, icon: <Ticket className="size-4" />, run: () => onOpenTask(x.id) });
    if (lower.length >= MIN_SEMANTIC && semantic.data) {
      // A ticket already listed by its title is not listed twice.
      const shown = new Set(tickets.map((x) => x.id));
      semantic.data.hits.filter((h) => !(h.kind === "ticket" && h.task_id !== null && shown.has(h.task_id))).forEach((h, i) => out.push(hitItem(h, i)));
    }
    for (const p of projects.filter((p) => p.id !== current.id && hit(p.name)).slice(0, lower ? 5 : 3))
      out.push({ key: `p-${p.id}`, group: "projects", label: p.name, icon: <FolderKanban className="size-4" />, run: () => onSelectProject(p.id) });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, areas, tasks.data, semantic.data, projects, current.id]);

  useEffect(() => {
    list.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [active]);
  if (!open) return null;

  const at = Math.min(active, Math.max(0, items.length - 1));
  const choose = (i: number) => {
    const it = items[i];
    if (!it?.run) return;
    onClose();
    it.run();
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") setActive(items.length ? (at + 1) % items.length : 0);
    else if (e.key === "ArrowUp") setActive(items.length ? (at - 1 + items.length) % items.length : 0);
    else if (e.key === "Enter") choose(at);
    else if (e.key === "Escape") onClose();
    else return;
    e.preventDefault();
  };
  const waiting = q.length >= MIN_SEMANTIC && (debounced !== q || semantic.isFetching);
  const state = semantic.data?.state;
  // Said once, quietly, where the history would be.
  const notice =
    q.length < MIN_SEMANTIC
      ? null
      : state === "off" || state === "unavailable"
        ? t(`palette.state.${state}`)
        : state === "preparing" && fresh
          ? t("palette.state.preparing")
          : fresh && !semantic.data!.hits.length
            ? t("palette.state.empty")
            : null;
  let last = "";
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/55 sm:p-4 sm:pt-[12vh]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("palette.open")}
        data-part="palette"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onKey}
        className="flex h-full w-full flex-col bg-bg pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] shadow-lg sm:h-auto sm:max-h-[72vh] sm:max-w-2xl sm:rounded-sm sm:pb-0 sm:ring-1 sm:ring-line-2"
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-3">
          <Search className="size-4 shrink-0 text-fg-3" />
          <input
            autoFocus
            role="combobox"
            aria-expanded
            aria-controls="palette-list"
            aria-activedescendant={items[at] ? `palette-${items[at].key}` : undefined}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            placeholder={t("palette.placeholder")}
            aria-label={t("palette.placeholder")}
            className="h-12 min-w-0 flex-1 bg-transparent outline-none placeholder:text-fg-3 touch:h-14"
          />
          {waiting && <Spinner className="size-4 text-fg-3" />}
          <button type="button" onClick={onClose} className="kbd hover:text-fg touch:px-2 touch:py-1">
            Esc
          </button>
        </div>
        <div ref={list} id="palette-list" role="listbox" className="scroll-thin min-h-0 flex-1 overflow-y-auto p-1.5">
          {items.map((it, i) => {
            const head = it.group !== last ? (last = it.group) : null;
            const on = i === at;
            return (
              <div key={it.key}>
                {head && (
                  <div className="caps flex items-center gap-2 px-2 pt-2.5 pb-1 text-fg-3">
                    {t(`palette.${head}`)}
                    {head === "history" && <span className="font-normal normal-case tracking-normal">{t("palette.historyHint")}</span>}
                  </div>
                )}
                <button
                  id={`palette-${it.key}`}
                  type="button"
                  role="option"
                  aria-selected={on}
                  data-active={on}
                  tabIndex={-1}
                  onMouseMove={() => active !== i && setActive(i)}
                  onClick={() => choose(i)}
                  className={clsx("flex w-full gap-2.5 rounded-sm px-2 text-left", it.snippet ? "items-start py-1.5" : "h-9 items-center touch:h-11", on ? "bg-sunk text-fg" : "text-fg-2")}
                >
                  <span className={clsx("shrink-0 text-fg-3", it.snippet && "pt-0.5")}>{it.icon}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-2">
                      {it.kind && <span className="shrink-0 text-fg-3">{it.kind}</span>}
                      {it.ref && <span className="shrink-0 font-mono text-fg-3 tabular-nums">{it.ref}</span>}
                      <span className="min-w-0 flex-1 truncate">{it.label}</span>
                      {it.age && <span className="shrink-0 text-fg-3">{it.age}</span>}
                      {it.external && <ExternalLink className="size-3.5 shrink-0 text-fg-3" />}
                    </span>
                    {it.snippet && <span className="mt-0.5 line-clamp-2 block text-fg-3">{it.snippet}</span>}
                  </span>
                  {on && !it.snippet && <CornerDownLeft className="size-3.5 shrink-0 text-fg-3" />}
                </button>
              </div>
            );
          })}
          {!items.length && !waiting && !notice && <p className="px-2 py-3 text-fg-3">{t("palette.none")}</p>}
          {notice && <p className="px-2 pt-2.5 pb-1.5 text-fg-3">{notice}</p>}
        </div>
        <div className="hidden shrink-0 border-t border-line px-3 py-1.5 text-fg-3 sm:block">{t("palette.hint")}</div>
      </div>
    </div>
  );
}
