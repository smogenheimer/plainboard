import clsx from "clsx";
import { Check, ChevronDown, ChevronRight, FilePen, FilePlus, SquareTerminal, X } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Message } from "../../api/types";
import { useSharedStored } from "../../lib/hooks";

/** The user's own switch: show the code of edits, new files and commands in the chat details. */
export const useShowCode = () => useSharedStored<boolean>("plainboard.chatCode", true);

/** What the backend stores in `meta.code` of a tool step (see orchestration/tool_code.py). */
interface Code {
  kind: "edit" | "write" | "bash";
  path?: string;
  edits?: { old: string; new: string }[];
  content?: string;
  command?: string;
  truncated: boolean;
  length: number;
}

export type Row = { k: "+" | "-" | " " | "gap"; o?: number; n?: number; c: string };

/** Line diff of one edit: the lines both share stay, the rest is removed and added. Beyond a few
 * hundred lines it only trims what they share at both ends. */
export function diffLines(oldText: string, newText: string): Row[] {
  const a = oldText ? oldText.split("\n") : [];
  const b = newText ? newText.split("\n") : [];
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const ma = a.slice(head, a.length - tail);
  const mb = b.slice(head, b.length - tail);
  const ops: { k: "+" | "-" | " "; c: string }[] = a.slice(0, head).map((c) => ({ k: " ", c }));
  if (ma.length * mb.length > 90_000) {
    ops.push(...ma.map((c) => ({ k: "-" as const, c })), ...mb.map((c) => ({ k: "+" as const, c })));
  } else {
    const w = mb.length + 1;
    const t = new Uint16Array((ma.length + 1) * w);
    for (let i = ma.length - 1; i >= 0; i--) for (let j = mb.length - 1; j >= 0; j--) t[i * w + j] = ma[i] === mb[j] ? t[(i + 1) * w + j + 1] + 1 : Math.max(t[(i + 1) * w + j], t[i * w + j + 1]);
    let i = 0;
    let j = 0;
    while (i < ma.length || j < mb.length) {
      if (i < ma.length && j < mb.length && ma[i] === mb[j]) ops.push({ k: " ", c: ma[i++] }), j++;
      else if (j < mb.length && (i === ma.length || t[i * w + j + 1] >= t[(i + 1) * w + j])) ops.push({ k: "+", c: mb[j++] });
      else ops.push({ k: "-", c: ma[i++] });
    }
  }
  ops.push(...a.slice(a.length - tail).map((c) => ({ k: " " as const, c })));
  let o = 1;
  let n = 1;
  return ops.map((op) => (op.k === "+" ? { ...op, n: n++ } : op.k === "-" ? { ...op, o: o++ } : { ...op, o: o++, n: n++ }));
}

function editRows(edits: { old: string; new: string }[]): Row[] {
  return edits.flatMap((e, i) => (i === 0 ? diffLines(e.old, e.new) : [{ k: "gap" as const, c: "" }, ...diffLines(e.old, e.new)]));
}

function writeRows(content: string): Row[] {
  return content.replace(/\n$/, "").split("\n").map((c, i) => ({ k: "+" as const, n: i + 1, c }));
}

const count = (rows: Row[], k: "+" | "-") => rows.filter((r) => r.k === k).length;

const KEYWORD = /^(async|await|def|class|return|yield|for|in|if|elif|else|while|is|not|and|or|None|True|False|import|from|with|as|try|except|finally|raise|lambda|pass|continue|break|const|let|var|function|export|default|new|null|undefined|true|false|fn|pub|impl|struct|enum|match|use|mut|self|this|type|interface|extends|throw|catch|switch|case|select|where)$/;

/** Light syntax colour from the semantic tokens only: keywords strong, strings `ok`, comments muted. */
function highlight(code: string, hash: boolean): ReactNode {
  const out: ReactNode[] = [];
  const re = new RegExp(`(${hash ? "#" : "//|#"}.*$)|("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\`(?:[^\`\\\\]|\\\\.)*\`)|(\\b\\d+(?:\\.\\d+)?\\b)|([A-Za-z_]\\w*)`, "g");
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(code))) {
    if (m.index > last) out.push(code.slice(last, m.index));
    const [tok, comment, string, num, word] = m;
    if (comment) out.push(<span key={i++} className="text-fg-3 italic">{tok}</span>);
    else if (string) out.push(<span key={i++} className="text-ok">{tok}</span>);
    else if (num) out.push(<span key={i++} className="font-semibold text-fg">{tok}</span>);
    else if (word && KEYWORD.test(word)) out.push(<span key={i++} className="font-bold text-fg">{tok}</span>);
    else out.push(tok);
    last = m.index + tok.length;
  }
  out.push(code.slice(last));
  return out;
}

const ADD = "bg-[color-mix(in_oklab,var(--ok)_15%,var(--bg))]";
const DEL = "bg-[color-mix(in_oklab,var(--del)_15%,var(--bg))]";

function Diff({ rows, hash }: { rows: Row[]; hash: boolean }) {
  return (
    <div className="max-h-80 overflow-auto font-mono leading-[1.45]">
      <div className="min-w-max">
        {rows.map((r, i) =>
          r.k === "gap" ? (
            <div key={i} className="my-1 border-t border-dashed border-line-2" />
          ) : (
            <div key={i} className={clsx("flex", r.k === "+" && ADD, r.k === "-" && DEL)}>
              <span className="w-9 shrink-0 pr-2 text-right text-fg-3 opacity-70 select-none">{r.k === "+" ? r.n : r.o}</span>
              <span className={clsx("w-4 shrink-0 text-center select-none", r.k === "+" ? "text-ok" : r.k === "-" ? "text-del" : "text-fg-3")}>{r.k === " " ? "" : r.k === "-" ? "−" : "+"}</span>
              <span className={clsx("pr-3 whitespace-pre", r.k === "-" && "opacity-80")}>{highlight(r.c, hash)}</span>
            </div>
          ),
        )}
      </div>
    </div>
  );
}

function Stat({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="shrink-0 font-mono whitespace-nowrap">
      <span className="text-ok">+{added}</span>
      {removed > 0 && <span className="ml-1.5 text-del">−{removed}</span>}
    </span>
  );
}

/** The path with its directory muted and the file name strong; when it is too long the directory gives way. */
function Path({ path }: { path: string }) {
  const cut = path.lastIndexOf("/") + 1;
  return (
    <span className="min-w-0 truncate font-mono" dir="rtl" title={path}>
      <bdi>
        <span className="text-fg-3">{path.slice(0, cut)}</span>
        <span className="font-semibold text-fg">{path.slice(cut)}</span>
      </bdi>
    </span>
  );
}

const ROW = "flex w-full items-center gap-2 px-4 py-1.5 text-left hover:bg-sunk";

/** A tool step that carries code: one compact line that opens to the diff, the new file or the command with its output. */
export function CodeLine({ message }: { message: Message }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { code, result, is_error, result_truncated } = message.meta as { code: Code; result?: string; is_error?: boolean; result_truncated?: boolean };
  const pending = result === undefined;
  const rows = useMemo(() => (!open ? [] : code.kind === "edit" ? editRows(code.edits ?? []) : code.kind === "write" ? writeRows(code.content ?? "") : []), [open, code]);
  const chevron = open ? <ChevronDown className="size-4 text-fg-3" /> : <ChevronRight className="size-4 text-fg-3" />;
  const hash = /\.(py|sh|ya?ml|toml|rb|conf|cfg|ini|txt)$/.test(code.path ?? "");
  const failed = is_error ? "text-fg line-through decoration-fg-3" : "text-fg-2";
  const note = (extra?: string) => (
    <div className="space-y-1 border-t border-line bg-bg px-4 py-1.5 text-fg-3">
      {code.truncated && <p>{t("chat.code.truncated", { count: code.length })}</p>}
      {extra && <p className="break-words whitespace-pre-wrap text-fg-2">{extra}</p>}
    </div>
  );

  if (code.kind === "bash") {
    return (
      <div data-part="note" className="border-b border-line">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className={clsx(ROW, "text-fg-3", open && "bg-sunk")}>
          <SquareTerminal className={clsx("size-4 shrink-0", pending && "animate-pulse text-fg")} />
          <span className={clsx("min-w-0 truncate font-mono", failed)}>$ {(code.command ?? "").split("\n")[0]}</span>
          <span className="ml-auto flex shrink-0 items-center gap-1">
            {pending ? null : is_error ? <X className="size-4 text-del" aria-label="error" /> : <Check className="size-4 text-ok" aria-label="ok" />}
            {chevron}
          </span>
        </button>
        {open && (
          <div className="space-y-1.5 border-t border-line bg-bg px-4 py-2 font-mono">
            <pre className="max-h-40 overflow-auto break-words whitespace-pre-wrap text-fg">
              <span className="text-fg-3">$ </span>
              {code.command}
            </pre>
            {!pending && <pre className={clsx("max-h-64 overflow-auto break-words whitespace-pre-wrap", is_error ? "text-del" : "text-fg-2")}>{result || t("chat.code.empty")}</pre>}
            {(code.truncated || result_truncated) && <p className="font-sans text-fg-3">{t("chat.code.truncated", { count: result_truncated ? (result?.length ?? 0) : code.length })}</p>}
          </div>
        )}
      </div>
    );
  }

  const write = code.kind === "write";
  const Icon = write ? FilePlus : FilePen;
  return (
    <div data-part="note" className="border-b border-line">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className={clsx(ROW, open && "bg-sunk")}>
        <Icon className={clsx("size-4 shrink-0 text-fg-3", pending && "animate-pulse text-fg")} />
        <span className={clsx("shrink-0 font-mono", failed)}>{message.content}</span>
        <Path path={code.path ?? ""} />
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {write ? <Stat added={writeRows(code.content ?? "").length} removed={0} /> : <EditStat edits={code.edits ?? []} />}
          {chevron}
        </span>
      </button>
      {open && (
        <div className="border-t border-line bg-bg py-1.5">
          <Diff rows={rows} hash={hash} />
          {(code.truncated || (is_error && result)) && note(is_error ? result : undefined)}
        </div>
      )}
    </div>
  );
}

function EditStat({ edits }: { edits: { old: string; new: string }[] }) {
  const rows = useMemo(() => editRows(edits), [edits]);
  return <Stat added={count(rows, "+")} removed={count(rows, "-")} />;
}
