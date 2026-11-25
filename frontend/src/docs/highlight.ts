/** A small colouring of code for the doc sites: comments, strings, numbers and keywords of the usual languages.
 * Not a parser: it never changes the text, only marks parts of it. Returns escaped HTML. */

const KEYWORDS = new Set(
  (
    "abstract as async await break case catch class const continue def default defer del do elif else enum except export extends false " +
    "final finally fn for from func function go if impl implements import in interface is let match mod module mut new nil None not null " +
    "or package pass private protected pub public raise readonly return self static struct super switch this throw throws trait True true " +
    "False try type typeof undefined use var void while with yield lambda val when where select insert update delete into values create table " +
    "alter drop join left right inner on group by order having limit and set echo export fi then done esac local"
  ).split(" "),
);
const CASE_FREE = new Set(["sql"]);
const HASH_COMMENT = new Set(["py", "python", "sh", "bash", "shell", "console", "yaml", "yml", "toml", "ruby", "rb", "ini", "env", "dockerfile", "docker", "r", "perl", "makefile", "conf"]);
const PLAIN = new Set(["text", "txt", "plain", "", "mermaid"]);

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function highlight(code: string, lang: string): string {
  if (PLAIN.has(lang) || code.length > 100_000) return esc(code);
  if (lang === "diff" || lang === "patch") {
    return code
      .split("\n")
      .map((line) => {
        const cls = line.startsWith("+") ? "tok-add" : line.startsWith("-") ? "tok-del" : line.startsWith("@@") ? "tok-c" : "";
        return cls ? `<span class="${cls}">${esc(line)}</span>` : esc(line);
      })
      .join("\n");
  }
  const hash = HASH_COMMENT.has(lang);
  const slashes = !hash || lang === "dockerfile";
  const sql = CASE_FREE.has(lang);
  const parts = [
    String.raw`(?<c>${slashes ? String.raw`\/\/[^\n]*|\/\*[\s\S]*?\*\/|` : ""}${hash ? "#[^\\n]*|" : ""}${sql ? "--[^\\n]*|" : ""}<!--[\s\S]*?-->)`,
    String.raw`(?<s>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|\`(?:[^\`\\]|\\.)*\`)`,
    String.raw`(?<n>\b(?:0x[\da-f]+|\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?)\b)`,
    String.raw`(?<w>[A-Za-z_$][\w$]*)`,
  ];
  const re = new RegExp(parts.join("|"), "gi");
  let out = "";
  let last = 0;
  for (const m of code.matchAll(re)) {
    const g = m.groups!;
    let cls = "";
    if (g.c !== undefined) cls = "tok-c";
    else if (g.s !== undefined) {
      // A key in JSON or YAML-like text: the string before a colon.
      cls = /^\s*:/.test(code.slice(m.index + m[0].length, m.index + m[0].length + 3)) ? "tok-p" : "tok-s";
    } else if (g.n !== undefined) cls = "tok-n";
    else if (g.w !== undefined) {
      const word = sql ? g.w.toLowerCase() : g.w;
      if (KEYWORDS.has(word)) cls = "tok-k";
      else if (code[m.index + g.w.length] === "(") cls = "tok-f";
    }
    if (!cls) continue;
    out += esc(code.slice(last, m.index)) + `<span class="${cls}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(code.slice(last));
}
