import clsx from "clsx";
import { CornerDownLeft, FileText, Loader2, Search as SearchIcon, X } from "lucide-react";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { search } from "./api";
import type { SearchHit, SiteView } from "./types";

const DEBOUNCE_MS = 180;

/** Ctrl+K: the site's own search in the language that is read, by words and (where the server has it) by meaning. */
export function SearchDialog({ site, lang, open, onClose, onOpen }: { site: SiteView; lang: string; open: boolean; onClose: () => void; onOpen: (url: string) => void }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const q = query.trim();

  useEffect(() => {
    if (open) {
      setActive(0);
      requestAnimationFrame(() => input.current?.select());
    }
  }, [open]);

  useEffect(() => {
    if (!q) {
      setHits(null);
      setBusy(false);
      return;
    }
    let gone = false;
    setBusy(true);
    const timer = window.setTimeout(() => {
      search(site, lang, q)
        .then((found) => {
          if (gone) return;
          setHits(found);
          setError("");
          setActive(0);
        })
        .catch((e: unknown) => !gone && setError(e instanceof Error && e.message === "429" ? t("docsSite.searchBusy") : t("docsSite.searchFailed")))
        .finally(() => !gone && setBusy(false));
    }, DEBOUNCE_MS);
    return () => {
      gone = true;
      window.clearTimeout(timer);
    };
  }, [q, site, lang, t]);

  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;
  const terms = q.toLowerCase().split(/\s+/).filter((w) => w.length > 1);
  const choose = (hit: SearchHit) => {
    onClose();
    onOpen(hit.url);
  };
  return (
    <div className="doc-scrim" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("docsSite.search")}
        className="doc-search"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          else if (e.key === "ArrowDown" && hits?.length) {
            e.preventDefault();
            setActive((a) => (a + 1) % hits.length);
          } else if (e.key === "ArrowUp" && hits?.length) {
            e.preventDefault();
            setActive((a) => (a - 1 + hits.length) % hits.length);
          } else if (e.key === "Enter" && hits?.[active]) {
            e.preventDefault();
            choose(hits[active]);
          }
        }}
      >
        <div className="doc-search-field">
          {busy ? <Loader2 className="size-5 shrink-0 animate-spin text-fg-3" /> : <SearchIcon className="size-5 shrink-0 text-fg-3" />}
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("docsSite.searchPlaceholder", { name: site.name })}
            aria-label={t("docsSite.search")}
            autoComplete="off"
            spellCheck={false}
            enterKeyHint="search"
          />
          <button type="button" className="doc-icon-btn" onClick={onClose} aria-label={t("docsSite.close")}>
            <X className="size-4" />
          </button>
        </div>
        <div className="doc-search-body">
          {error ? (
            <p className="doc-search-note">{error}</p>
          ) : !q ? (
            <p className="doc-search-note">{t("docsSite.searchHint")}</p>
          ) : hits && hits.length === 0 && !busy ? (
            <p className="doc-search-note">{t("docsSite.noHits", { query: q })}</p>
          ) : (
            <ul ref={list} role="listbox" aria-label={t("docsSite.results")}>
              {(hits ?? []).map((hit, i) => (
                <li key={hit.url} role="option" aria-selected={i === active} data-index={i}>
                  <a
                    href={hit.url}
                    className={clsx("doc-hit", i === active && "is-active")}
                    onMouseMove={() => setActive(i)}
                    onClick={(e) => {
                      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
                      e.preventDefault();
                      choose(hit);
                    }}
                  >
                    <FileText className="mt-0.5 size-4 shrink-0 text-fg-3" />
                    <span className="min-w-0 flex-1">
                      <span className="doc-hit-title">{mark(hit.title, terms)}</span>
                      {hit.path && <span className="doc-hit-path">{hit.path.split("/").join(" › ")}</span>}
                      {hit.snippet && <span className="doc-hit-snippet">{mark(hit.snippet.replace(/\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/gi, ""), terms)}</span>}
                    </span>
                    {i === active && <CornerDownLeft className="mt-0.5 size-4 shrink-0 text-fg-3" />}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="doc-search-foot">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> {t("docsSite.keysMove")}
          </span>
          <span>
            <kbd>↵</kbd> {t("docsSite.keysOpen")}
          </span>
          <span>
            <kbd>Esc</kbd> {t("docsSite.keysClose")}
          </span>
        </div>
      </div>
    </div>
  );
}

/** The searched words, marked in a text. */
function mark(text: string, terms: string[]): ReactNode {
  if (!terms.length) return text;
  const re = new RegExp(`(${terms.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : <Fragment key={i}>{part}</Fragment>));
}
