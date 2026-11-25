import clsx from "clsx";
import { Check, ChevronsUpDown, Pencil, Plus, Search } from "lucide-react";
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useMe } from "../../api/queries";
import { Input } from "../../components/ui";
import { useDismiss, useInView } from "../../lib/hooks";
import type { Project } from "../../api/types";

/** From this many projects the menu gets a search field and lists the recently used ones first. */
const MANY = 8;
const RECENT = 5;

const fold = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

const recentKey = (userId: number) => `plainboard.recentProjects.${userId}`;
function readRecent(userId: number): number[] {
  try {
    const ids: unknown = JSON.parse(localStorage.getItem(recentKey(userId)) ?? "[]");
    return Array.isArray(ids) ? ids.filter((id): id is number => typeof id === "number") : [];
  } catch {
    return [];
  }
}

/** Recently used first (with a divider after them), then the rest in their order; a query filters both. */
function arrange(projects: Project[], recentIds: number[], query: string) {
  const byId = new Map(projects.map((p) => [p.id, p]));
  const recent = recentIds.map((id) => byId.get(id)).filter((p): p is Project => !!p);
  const rest = projects.filter((p) => !recent.includes(p));
  const q = fold(query.trim());
  if (q) return { list: [...recent, ...rest].filter((p) => fold(p.name).includes(q)), recentCount: 0 };
  return { list: [...recent, ...rest], recentCount: rest.length ? recent.length : 0 };
}

/** `side`: full-width field in the sidebar; `rail`: the initial only; `bar`: the phone's top bar. */
export function ProjectSwitcher({
  projects,
  current,
  onSelect,
  onNew,
  onEdit,
  variant = "side",
}: {
  projects: Project[];
  current: Project;
  onSelect: (id: number) => void;
  /** Missing: the user may not create projects, or does not own this one. */
  onNew?: () => void;
  onEdit?: () => void;
  variant?: "side" | "rail" | "bar";
}) {
  const { t } = useTranslation();
  const userId = useMe()?.id;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const [maxHeight, setMaxHeight] = useState<number>();
  const [recentIds, setRecentIds] = useState<number[]>([]);
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const many = projects.length >= MANY;

  // The opened project goes to the front of this user's recently used ones.
  useEffect(() => {
    if (userId === undefined) return;
    const ids = [current.id, ...readRecent(userId).filter((id) => id !== current.id)].slice(0, RECENT);
    localStorage.setItem(recentKey(userId), JSON.stringify(ids));
  }, [userId, current.id]);

  const { list, recentCount } = useMemo(() => arrange(projects, many ? recentIds : [], query), [projects, many, recentIds, query]);

  const [menuRef, inView] = useInView<HTMLDivElement>(open);
  useDismiss(ref, open, () => setOpen(false));

  // The menu never leaves the screen: the project list takes what room is below the button and scrolls.
  useLayoutEffect(() => {
    if (!open) return;
    const fit = () => {
      const bottom = ref.current?.getBoundingClientRect().bottom ?? 0;
      // The phone's tab bar lies above the menu: it ends where the bar starts.
      const tabs = document.querySelector("[data-part=tabs]");
      const end = tabs?.getClientRects().length ? tabs.getBoundingClientRect().top : window.innerHeight;
      setMaxHeight(Math.max(160, end - bottom - 12));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    // Focus the search at once on desktop; on phones and touch screens it would pop up the keyboard.
    if (many && !window.matchMedia("(max-width: 639px), (pointer: coarse)").matches) searchRef.current?.focus();
  }, [open, many]);

  // The marked project stays in view; with none marked (on open), the current one is.
  const at = active >= 0 ? active : list.findIndex((p) => p.id === current.id);
  useLayoutEffect(() => {
    if (open && at >= 0) itemRefs.current[at]?.scrollIntoView({ block: "nearest" });
  }, [at, open]);

  const toggle = () => {
    if (!open) {
      // The order is fixed while the menu is open.
      setRecentIds(many && userId !== undefined ? readRecent(userId) : []);
      setQuery("");
      setActive(-1);
    }
    setOpen(!open);
  };

  const choose = (id: number) => {
    onSelect(id);
    setOpen(false);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (!open) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!list.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      // Nothing marked yet: the keys start from the current project.
      setActive(at < 0 ? (step > 0 ? 0 : list.length - 1) : Math.min(list.length - 1, Math.max(0, at + step)));
    } else if (e.key === "Enter" && (e.target === searchRef.current || e.target === e.currentTarget.firstChild)) {
      const p = list[active];
      if (!p) return;
      e.preventDefault();
      choose(p.id);
    }
  };

  const item = "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-sunk touch:py-3";
  return (
    <div ref={ref} onKeyDown={onKeyDown} className={clsx("relative", variant === "bar" && "min-w-0")}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title={variant === "rail" ? current.name : undefined}
        onClick={toggle}
        className={clsx(
          "flex items-center gap-1.5 rounded-sm hover:bg-sunk",
          variant === "side" && "h-8 w-full px-2 font-semibold ring-1 ring-line-2 ring-inset",
          variant === "rail" && "size-[34px] justify-center font-extrabold ring-1 ring-line-2 ring-inset",
          variant === "bar" && "h-10 max-w-full px-2 font-extrabold",
        )}
      >
        {variant === "rail" ? (
          current.name[0]?.toUpperCase()
        ) : (
          <>
            <span className="truncate">{current.name}</span>
            <ChevronsUpDown className={clsx("size-3.5 shrink-0 text-fg-3", variant === "side" && "ml-auto")} />
          </>
        )}
      </button>
      {open && (
        <div
          role="menu"
          data-part="menu"
          ref={menuRef}
          style={{ maxHeight, ...inView }}
          className="absolute top-full left-0 z-40 mt-1 flex w-64 max-w-[calc(100vw-1rem)] flex-col rounded-sm bg-bg p-1 shadow-lg ring-1 ring-line-2"
        >
          {many && (
            <div className="relative mb-1 shrink-0">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-fg-3" />
              <Input
                ref={searchRef}
                type="search"
                className="pl-8"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActive(e.target.value.trim() ? 0 : -1);
                }}
                placeholder={t("projects.search")}
                aria-label={t("projects.search")}
              />
            </div>
          )}
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
            {list.map((p, i) => (
              <Fragment key={p.id}>
                {i === recentCount && i > 0 && <div className="mx-2 my-1 border-t border-line" />}
                <button
                  ref={(el) => {
                    itemRefs.current[i] = el;
                  }}
                  type="button"
                  role="menuitem"
                  onClick={() => choose(p.id)}
                  onMouseMove={() => active !== i && setActive(i)}
                  className={clsx(item, p.id === current.id && "font-semibold", i === active && "bg-sunk")}
                >
                  <span className="flex-1 truncate">{p.name}</span>
                  {p.id === current.id && <Check className="size-3.5 shrink-0" />}
                </button>
              </Fragment>
            ))}
            {!list.length && <p className="px-2 py-1.5 text-fg-3 touch:py-3">{t("projects.noMatch")}</p>}
          </div>
          {(onEdit || onNew) && <div className="my-1 shrink-0 border-t border-line" />}
          {onEdit && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                onEdit();
                setOpen(false);
              }}
              className={clsx(item, "shrink-0 text-fg-2")}
            >
              <Pencil className="size-3.5 shrink-0" /> {t("projects.settings")}
            </button>
          )}
          {onNew && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                onNew();
                setOpen(false);
              }}
              className={clsx(item, "shrink-0 text-fg-2")}
            >
              <Plus className="size-3.5 shrink-0" /> {t("projects.new")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
