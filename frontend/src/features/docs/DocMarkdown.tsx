import { Download } from "lucide-react";
import { memo, useMemo, useState, type ComponentProps, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Lightbox } from "../../components/Lightbox";
import { MermaidBlock } from "../../components/MermaidBlock";
import { Callout, CodeBlock, pictureMode, type CalloutKind } from "../../docs/Content";
import "../../docs/docs.css";
import { useLook } from "../../lib/theme";

type MdNode = { type: string; value?: string; children?: MdNode[]; data?: { hProperties?: Record<string, unknown> } };

/** Remark plugin: GitHub's callouts (a quote that starts with [!NOTE] …), as the served site draws them. */
function callouts() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (node.type === "blockquote") {
        const first = node.children?.[0];
        const text = first?.type === "paragraph" ? first.children?.[0] : undefined;
        const m = text?.type === "text" ? text.value?.match(/^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i) : null;
        if (m && text) {
          text.value = text.value!.slice(m[0].length);
          node.data = { ...node.data, hProperties: { dataCallout: m[1].toUpperCase() } };
        }
      }
      node.children?.forEach(walk);
    };
    walk(tree);
  };
}

const hastText = (node: { value?: string; children?: unknown[] }): string =>
  node.value ?? (node.children as { value?: string; children?: unknown[] }[] | undefined)?.map(hastText).join("") ?? "";

/** A doc page's Markdown in the app, drawn as the served site draws it (its classes and slop2gether's colours):
 * `doc-file:<id>` is the file, `doc-page:<id>` opens that page here. */
export const DocMarkdown = memo(function DocMarkdown({ text, onOpenPage }: { text: string; onOpenPage?: (id: number) => void }) {
  const look = useLook();
  const [zoom, setZoom] = useState<{ src: string; alt: string } | null>(null);
  const components = useMemo<Components>(
    () => ({
      a: ({ node: _n, href, children }: ComponentProps<"a"> & { node?: unknown }) => {
        const page = href?.match(/^#doc-page-(\d+)(#.*)?$/);
        if (page) {
          return (
            <a
              href={href}
              onClick={(e) => {
                e.preventDefault();
                onOpenPage?.(Number(page[1]));
              }}
            >
              {children}
            </a>
          );
        }
        if (href?.startsWith("/api/attachments/")) {
          return (
            <a href={`${href}?inline=false`} className="doc-file" download>
              <Download className="size-4 shrink-0" aria-hidden />
              <span>{children}</span>
            </a>
          );
        }
        return (
          <a href={href} target="_blank" rel="noreferrer" className="doc-external">
            {children}
          </a>
        );
      },
      img: ({ src: url, alt }) => {
        if (typeof url !== "string") return null;
        const { src, mode } = pictureMode(url);
        return (
          <button type="button" className="doc-img" data-mode={mode} onClick={() => setZoom({ src, alt: alt ?? "" })}>
            <img src={src} alt={alt ?? ""} loading="lazy" />
          </button>
        );
      },
      pre: ({ node }) => {
        const code = node?.children[0] as { properties?: { className?: string[] } } | undefined;
        const lang = code?.properties?.className?.find((c) => c.startsWith("language-"))?.slice(9).toLowerCase() ?? "";
        const body = (node ? hastText(node) : "").replace(/\n$/, "");
        const block = <CodeBlock text={body} lang={lang} />;
        return lang === "mermaid" ? (
          <div className="doc-diagram">
            <MermaidBlock code={body} open={false} fallback={block} />
          </div>
        ) : (
          block
        );
      },
      table: ({ node: _n, ...props }) => (
        <div className="doc-table">
          <table {...props} />
        </div>
      ),
      blockquote: ({ node, children }) => {
        const kind = (node?.properties as { dataCallout?: string } | undefined)?.dataCallout as CalloutKind | undefined;
        return kind ? <Callout kind={kind}>{children as ReactNode}</Callout> : <blockquote>{children}</blockquote>;
      },
    }),
    [onOpenPage],
  );
  return (
    <div data-design="slop2gether" data-theme={look.dark ? "slop2gether-dark" : "slop2gether-light"} className="doc-preview">
      <div className="doc-prose">
        <ReactMarkdown remarkPlugins={[remarkGfm, callouts]} components={components} urlTransform={docUrl}>
          {text}
        </ReactMarkdown>
      </div>
      {zoom && <Lightbox images={[zoom]} onClose={() => setZoom(null)} />}
    </div>
  );
});

/** The page's own references, then react-markdown's safe rule for the rest (no javascript: and the like). */
function docUrl(url: string): string {
  const file = url.match(/^doc-file:(\d+)(#[\w-]+)?$/);
  if (file) return `/api/attachments/${file[1]}${file[2] ?? ""}`;
  const page = url.match(/^doc-page:(\d+)(#[\w-]+)?$/);
  if (page) return `#doc-page-${page[1]}${page[2] ?? ""}`;
  return /^(https?:|mailto:|#|\/)/i.test(url) || !/^[a-z][a-z0-9+.-]*:/i.test(url) ? url : "";
}
