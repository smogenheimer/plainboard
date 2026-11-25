import clsx from "clsx";
import { AlertTriangle, Check, Copy, Download, Info, Lightbulb, Link2, OctagonAlert, Sparkles } from "lucide-react";
import { createElement, Fragment, memo, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Lightbox, type LightboxImage } from "../components/Lightbox";
import { MermaidBlock } from "../components/MermaidBlock";
import { useLook } from "../lib/theme";
import { highlight } from "./highlight";

/** The elements the server's Markdown (markdown-it, raw HTML off) makes; anything else is shown as its text. */
const TAGS = new Set([
  "p", "h1", "h2", "h3", "h4", "h5", "h6", "a", "img", "pre", "code", "em", "strong", "s", "del", "ul", "ol", "li", "blockquote",
  "table", "thead", "tbody", "tr", "th", "td", "hr", "br",
]); // prettier-ignore
const ATTRS = ["href", "src", "alt", "title", "id", "start"];

const CALLOUTS = {
  NOTE: { icon: Info, tone: "note" },
  TIP: { icon: Lightbulb, tone: "tip" },
  IMPORTANT: { icon: Sparkles, tone: "important" },
  WARNING: { icon: AlertTriangle, tone: "warning" },
  CAUTION: { icon: OctagonAlert, tone: "caution" },
} as const;
export type CalloutKind = keyof typeof CALLOUTS;

/** A picture for one mode (#125): its address ends in #light or #dark (or GitHub's #gh-light-mode-only …). */
const MODE = /#(?:gh-)?(light|dark)(?:-mode-only)?$/;
export type PictureMode = "light" | "dark";
export function pictureMode(src: string): { src: string; mode?: PictureMode } {
  const m = src.match(MODE);
  return m ? { src: src.slice(0, m.index), mode: m[1] as PictureMode } : { src };
}

/** The pictures the lightbox steps through: those of the mode in use; ``start`` in them for ``index`` of all. */
export function shownPictures(images: (LightboxImage & { mode?: PictureMode })[], index: number, dark: boolean) {
  const shown = images.filter((i) => !i.mode || i.mode === (dark ? "dark" : "light"));
  return { shown, start: Math.max(0, shown.indexOf(images[index])) };
}

export interface ContentProps {
  html: string;
  /** Where the site's files are: links there are files to download (or pictures). */
  filesBase: string;
  /** A link to a page of the site: shown without a reload. */
  onNavigate: (href: string) => void;
  onPrefetch: (href: string) => void;
}

/** A page's rendered Markdown as React: code blocks with copy and colours, diagrams, callouts, pictures in a lightbox,
 * files as downloads, headings with their anchor, tables that scroll. */
export const Content = memo(function Content({ html, filesBase, onNavigate, onPrefetch }: ContentProps) {
  const [open, setOpen] = useState<number | null>(null);
  const look = useLook();
  const { nodes, images } = useMemo(() => {
    const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
    const images: Picture[] = [];
    const ctx: Ctx = { filesBase, images, onNavigate, onPrefetch, zoom: setOpen };
    return { nodes: children(doc.body, ctx, "c"), images };
  }, [html, filesBase, onNavigate, onPrefetch]);
  return (
    <>
      {nodes}
      {open !== null && images.length > 0 && <Zoomed images={images} index={open} dark={look.dark} onClose={() => setOpen(null)} />}
    </>
  );
});

function Zoomed({ images, index, dark, onClose }: { images: Picture[]; index: number; dark: boolean; onClose: () => void }) {
  const { shown, start } = shownPictures(images, index, dark);
  return shown.length > 0 ? <Lightbox images={shown} start={start} onClose={onClose} /> : null;
}

type Picture = LightboxImage & { mode?: PictureMode };

interface Ctx {
  filesBase: string;
  images: Picture[];
  onNavigate: (href: string) => void;
  onPrefetch: (href: string) => void;
  zoom: (index: number) => void;
}

function children(el: Element, ctx: Ctx, key: string): ReactNode[] {
  return Array.from(el.childNodes).map((n, i) => node(n, ctx, `${key}.${i}`));
}

function attrs(el: Element, key: string): Record<string, unknown> {
  const out: Record<string, unknown> = { key };
  for (const name of ATTRS) {
    const value = el.getAttribute(name);
    if (value !== null) out[name] = value;
  }
  const align = el.getAttribute("style")?.match(/text-align:\s*(left|right|center)/)?.[1];
  if (align) out.style = { textAlign: align };
  return out;
}

function node(n: Node, ctx: Ctx, key: string): ReactNode {
  if (n.nodeType === Node.TEXT_NODE) return n.textContent;
  if (n.nodeType !== Node.ELEMENT_NODE) return null;
  const el = n as Element;
  const tag = el.tagName.toLowerCase();
  if (!TAGS.has(tag)) return <Fragment key={key}>{el.textContent}</Fragment>;
  switch (tag) {
    case "h1":
    case "h2":
    case "h3":
    case "h4":
    case "h5":
    case "h6":
      return (
        <Heading key={key} tag={tag} id={el.id}>
          {children(el, ctx, key)}
        </Heading>
      );
    case "pre": {
      const code = el.querySelector("code");
      const lang = code?.className.match(/language-([\w+#.-]+)/)?.[1]?.toLowerCase() ?? "";
      const text = (code ?? el).textContent?.replace(/\n$/, "") ?? "";
      const block = <CodeBlock text={text} lang={lang} />;
      return lang === "mermaid" ? (
        <div key={key} className="doc-diagram">
          <MermaidBlock code={text} open={false} fallback={block} />
        </div>
      ) : (
        <Fragment key={key}>{block}</Fragment>
      );
    }
    case "img": {
      const src = el.getAttribute("src") ?? "";
      const alt = el.getAttribute("alt") ?? "";
      const mode = (el.getAttribute("data-mode") as PictureMode | null) ?? undefined;
      const index = ctx.images.push({ src, alt, mode }) - 1;
      return (
        <button key={key} type="button" className="doc-img" data-mode={mode} onClick={() => ctx.zoom(index)} aria-label={alt || undefined}>
          <img src={src} alt={alt} loading="lazy" decoding="async" />
        </button>
      );
    }
    case "a":
      return link(el, ctx, key);
    case "table":
      return (
        <div key={key} className="doc-table">
          <table>{children(el, ctx, key)}</table>
        </div>
      );
    case "blockquote": {
      const kind = calloutOf(el);
      if (kind) return callout(el, kind, ctx, key);
      break;
    }
    case "br":
    case "hr":
      return createElement(tag, { key });
  }
  return createElement(tag, attrs(el, key), ...children(el, ctx, key));
}

function link(el: Element, ctx: Ctx, key: string): ReactNode {
  const href = el.getAttribute("href") ?? "";
  const url = new URL(href, location.href);
  const inner = children(el, ctx, key);
  if (url.origin === location.origin && url.pathname.startsWith(ctx.filesBase + "/")) {
    // A picture in a link is a picture; a file is a download with its name.
    if (el.querySelector("img")) return <a key={key} href={href} target="_blank" rel="noreferrer">{inner}</a>;
    return (
      <a key={key} href={href} className="doc-file" download>
        <Download className="size-4 shrink-0" aria-hidden />
        <span>{inner}</span>
      </a>
    );
  }
  if (href === "#") return <span key={key}>{inner}</span>; // a page link whose page is gone
  if (url.origin !== location.origin) {
    return (
      <a key={key} href={href} target="_blank" rel="noreferrer noopener" className="doc-external">
        {inner}
      </a>
    );
  }
  return (
    <a
      key={key}
      href={href}
      onPointerEnter={() => ctx.onPrefetch(href)}
      onFocus={() => ctx.onPrefetch(href)}
      onClick={(e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        ctx.onNavigate(href);
      }}
    >
      {inner}
    </a>
  );
}

/** GitHub's notation: a quote that starts with [!NOTE], [!TIP], [!IMPORTANT], [!WARNING] or [!CAUTION]. */
function calloutOf(el: Element): CalloutKind | null {
  const first = el.firstElementChild;
  if (first?.tagName !== "P") return null;
  const m = first.textContent?.match(/^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]/i);
  return m ? (m[1].toUpperCase() as CalloutKind) : null;
}

function callout(el: Element, kind: CalloutKind, ctx: Ctx, key: string): ReactNode {
  const copy = el.cloneNode(true) as Element;
  const first = copy.firstElementChild!;
  // Take the marker off the first text, and the line break after it.
  const walker = document.createTreeWalker(first, NodeFilter.SHOW_TEXT);
  const text = walker.nextNode();
  if (text?.textContent) text.textContent = text.textContent.replace(/^\s*\[![a-z]+\]\s*/i, "");
  while (first.firstChild && (first.firstChild.nodeName === "BR" || (first.firstChild.nodeType === Node.TEXT_NODE && !first.firstChild.textContent?.trim()))) {
    first.firstChild.remove();
  }
  if (!first.textContent?.trim() && !first.querySelector("img")) first.remove();
  return (
    <Callout key={key} kind={kind}>
      {children(copy, ctx, key)}
    </Callout>
  );
}

export function Callout({ kind, children }: { kind: CalloutKind; children: ReactNode }) {
  const { t } = useTranslation();
  const { icon: Icon, tone } = CALLOUTS[kind];
  return (
    <aside className="doc-callout" data-tone={tone}>
      <p className="doc-callout-title">
        <Icon className="size-4" aria-hidden />
        {t(`docsSite.callout.${kind.toLowerCase()}`)}
      </p>
      <div>{children}</div>
    </aside>
  );
}

function Heading({ tag, id, children }: { tag: string; id: string; children: ReactNode }) {
  const { t } = useTranslation();
  if (!id) return createElement(tag, null, children);
  return createElement(
    tag,
    { id, className: "doc-heading" },
    children,
    <a
      href={`#${id}`}
      className="doc-anchor"
      aria-label={t("docsSite.anchor")}
      title={t("docsSite.anchor")}
      onClick={(e) => {
        e.preventDefault();
        history.replaceState(history.state, "", `#${id}`);
        document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
        void copy(location.href);
      }}
    >
      <Link2 className="size-4" aria-hidden />
    </a>,
  );
}

/** Copies text; where the clipboard API is missing (plain HTTP) through a hidden field. */
export async function copy(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // the field below
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(area);
  area.select();
  try {
    document.execCommand("copy");
  } finally {
    area.remove();
  }
}

const LANG_NAMES: Record<string, string> = {
  js: "JavaScript", javascript: "JavaScript", ts: "TypeScript", typescript: "TypeScript", tsx: "TSX", jsx: "JSX", py: "Python", python: "Python",
  sh: "Shell", bash: "Bash", shell: "Shell", console: "Console", json: "JSON", yaml: "YAML", yml: "YAML", toml: "TOML", sql: "SQL", go: "Go",
  rust: "Rust", rs: "Rust", java: "Java", kotlin: "Kotlin", html: "HTML", css: "CSS", md: "Markdown", markdown: "Markdown", diff: "Diff",
  dockerfile: "Dockerfile", docker: "Dockerfile", c: "C", cpp: "C++", cs: "C#", csharp: "C#", php: "PHP", ruby: "Ruby", rb: "Ruby", swift: "Swift",
  ini: "INI", env: ".env", http: "HTTP", graphql: "GraphQL", xml: "XML", text: "Text", txt: "Text",
}; // prettier-ignore

export function CodeBlock({ text, lang }: { text: string; lang: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>(undefined);
  const html = useMemo(() => highlight(text, lang), [text, lang]);
  const label = t(copied ? "docsSite.copied" : "docsSite.copy");
  return (
    <div className="doc-code">
      <div className="doc-code-head">
        <span>{LANG_NAMES[lang] ?? (lang || t("docsSite.code"))}</span>
        <button
          type="button"
          className={clsx("doc-copy", copied && "is-done")}
          onClick={async () => {
            await copy(text);
            setCopied(true);
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => setCopied(false), 1600);
          }}
          aria-label={label}
          title={label}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          <span>{label}</span>
        </button>
      </div>
      <pre>
        <code dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}
