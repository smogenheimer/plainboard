import clsx from "clsx";
import { Sparkle, X } from "lucide-react";
import { lazy, Suspense, useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDismiss } from "../lib/hooks";
import type { PageView, SiteView } from "./types";

// The panel (Markdown and all) loads only once someone reaches for the button: the site itself stays light.
const loadPanel = () => import("./AssistantPanel");
const AssistantPanel = lazy(loadPanel);

/** The site's AI assistant (#119): a round button bottom right that opens a chat about the documentation. Once
 * opened the panel stays mounted, so the conversation (and an answer still coming) survives closing it and
 * moving between pages. */
export function Assistant({ site, lang, page, onNavigate }: { site: SiteView; lang: string; page: PageView | null; onNavigate: (href: string) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [unread, setUnread] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const openRef = useRef(open);
  openRef.current = open;

  const close = useCallback(() => {
    setOpen(false);
    launcher.current?.focus({ preventScroll: true });
  }, []);
  useDismiss(box, open, close);

  const toggle = () => {
    setLoaded(true);
    setUnread(false);
    setOpen((o) => !o);
  };
  const onAnswer = useCallback(() => {
    if (!openRef.current) setUnread(true);
  }, []);

  return (
    <div ref={box} className={clsx("doc-ai", open && "is-open")}>
      {loaded && (
        <Suspense fallback={null}>
          <AssistantPanel site={site} lang={lang} page={page} open={open} onClose={close} onNavigate={onNavigate} onAnswer={onAnswer} />
        </Suspense>
      )}
      <button
        ref={launcher}
        type="button"
        className="doc-ai-launcher"
        onClick={toggle}
        onPointerEnter={() => void loadPanel()}
        onFocus={() => void loadPanel()}
        aria-expanded={open}
        aria-controls="doc-ai-panel"
        aria-label={t(open ? "docsSite.assistant.close" : "docsSite.assistant.open")}
        title={t(open ? "docsSite.assistant.close" : "docsSite.assistant.open")}
      >
        <Sparkle className={clsx("doc-ai-launcher-icon size-[22px]", open && "is-gone")} aria-hidden />
        <X className={clsx("doc-ai-launcher-icon size-[22px]", !open && "is-gone")} aria-hidden />
        {unread && <span className="doc-ai-dot" aria-hidden />}
      </button>
    </div>
  );
}
