import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLook } from "../lib/theme";
import { Lightbox } from "./Lightbox";

type Mermaid = (typeof import("mermaid"))["default"];

// Loaded on the first diagram only: the library is large and most messages have none.
let loading: Promise<Mermaid> | undefined;
const loadMermaid = () => (loading ??= import("mermaid").then((m) => m.default));

let counter = 0;
let configured = "";
// Mermaid's configuration is global: one diagram is configured and drawn at a time.
let queue: Promise<unknown> = Promise.resolve();
function draw<T>(job: (mermaid: Mermaid) => Promise<T>): Promise<T> {
  const next = queue.then(() => loadMermaid().then(job));
  queue = next.catch(() => undefined);
  return next;
}

/** A CSS colour (oklch, color-mix, …) as #rrggbb, which Mermaid can compute with. */
function hex(color: string, ctx: CanvasRenderingContext2D): string {
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillStyle = "#000";
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** Relative luminance of #rrggbb (WCAG). */
function luminance(color: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(color.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const contrast = (a: string, b: string) => {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
};

/** The 10 data colours of the design (`--chart-1…`), see the design stylesheets. */
const CHART_COLORS = 10;

/** Mermaid's theme from the design's colour tokens, as they apply where the diagram stands. Every
 * diagram's own colours come from the design's data colours, with text that reads on them. */
function themeVariables(el: Element, dark: boolean) {
  const style = getComputedStyle(el);
  const ctx = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!;
  const token = (name: string) => hex(style.getPropertyValue(name).trim(), ctx);
  const mix = (a: string, b: string, share: number) => hex(`color-mix(in oklab, ${a} ${share}%, ${b})`, ctx);
  const [bg, sunk, line, line2, fg, fg2, fg3, you, youBg, del] = ["--bg", "--sunk", "--line", "--line-2", "--fg", "--fg-2", "--fg-3", "--you", "--you-bg", "--del"].map(token);
  const data = Array.from({ length: CHART_COLORS }, (_, i) => token(`--chart-${i + 1}`));
  const on = (color: string) => (contrast(color, fg) >= contrast(color, bg) ? fg : bg);
  const at = (i: number) => data[i % data.length];
  const scale: Record<string, string> = {};
  for (let i = 0; i < 12; i++) {
    scale[`cScale${i}`] = at(i);
    scale[`cScalePeer${i}`] = mix(at(i), fg, 70);
    scale[`cScaleInv${i}`] = mix(at(i), fg, 70);
    scale[`cScaleLabel${i}`] = on(at(i));
    scale[`pie${i + 1}`] = at(i);
  }
  for (let i = 0; i < 8; i++) {
    scale[`git${i}`] = at(i);
    scale[`gitInv${i}`] = on(at(i));
    scale[`gitBranchLabel${i}`] = on(at(i));
    scale[`fillType${i}`] = at(i);
  }
  for (let i = 0; i < 6; i++) scale[`actor${i}`] = at(i); // journey's people
  const tint = (color: string) => mix(color, bg, dark ? 22 : 16);
  return {
    darkMode: dark,
    background: bg,
    fontFamily: style.getPropertyValue("--font-sans").trim() || "sans-serif",
    fontSize: "14px",
    primaryColor: sunk,
    primaryTextColor: fg,
    primaryBorderColor: line2,
    secondaryColor: youBg,
    secondaryTextColor: fg,
    secondaryBorderColor: you,
    tertiaryColor: bg,
    tertiaryTextColor: fg,
    tertiaryBorderColor: line,
    lineColor: fg3,
    textColor: fg,
    mainBkg: sunk,
    nodeBorder: line2,
    clusterBkg: bg,
    clusterBorder: line,
    titleColor: fg,
    edgeLabelBackground: bg,
    noteBkgColor: youBg,
    noteTextColor: fg,
    noteBorderColor: you,
    actorBkg: sunk,
    actorBorder: line2,
    actorTextColor: fg,
    actorLineColor: fg3,
    signalColor: fg2,
    signalTextColor: fg,
    labelBoxBkgColor: sunk,
    labelBoxBorderColor: line2,
    labelTextColor: fg,
    loopTextColor: fg,
    activationBkgColor: line,
    activationBorderColor: line2,
    sequenceNumberColor: bg,
    ...scale,
    scaleLabelColor: on(data[0]),
    // Pie
    pieStrokeColor: bg,
    pieStrokeWidth: "2px",
    pieOuterStrokeColor: line2,
    pieOuterStrokeWidth: "1px",
    pieOpacity: "1",
    pieTitleTextColor: fg,
    pieTitleTextSize: "16px",
    pieSectionTextColor: on(data[0]),
    pieSectionTextSize: "14px",
    pieLegendTextColor: fg,
    pieLegendTextSize: "14px",
    // xychart
    xyChart: {
      backgroundColor: bg,
      titleColor: fg,
      dataLabelColor: fg,
      legendTextColor: fg,
      xAxisTitleColor: fg2,
      xAxisLabelColor: fg2,
      xAxisTickColor: line2,
      xAxisLineColor: line2,
      yAxisTitleColor: fg2,
      yAxisLabelColor: fg2,
      yAxisTickColor: line2,
      yAxisLineColor: line2,
      plotColorPalette: data.join(","),
    },
    // Quadrant chart
    quadrant1Fill: tint(at(0)),
    quadrant2Fill: tint(at(1)),
    quadrant3Fill: tint(at(2)),
    quadrant4Fill: tint(at(3)),
    quadrant1TextFill: fg,
    quadrant2TextFill: fg,
    quadrant3TextFill: fg,
    quadrant4TextFill: fg,
    quadrantPointFill: fg,
    quadrantPointTextFill: fg,
    quadrantXAxisTextFill: fg2,
    quadrantYAxisTextFill: fg2,
    quadrantInternalBorderStrokeFill: line2,
    quadrantExternalBorderStrokeFill: line2,
    quadrantTitleFill: fg,
    // Radar
    radar: { axisColor: fg3, graticuleColor: line, graticuleOpacity: 0.5, curveOpacity: 0.25, axisLabelFontSize: 13, legendFontSize: 13 },
    // Gantt: bars in the first data colour, text that reads on it; done, active and critical
    // tasks are tints with the text colour.
    sectionBkgColor: tint(at(0)),
    sectionBkgColor2: tint(at(1)),
    altSectionBkgColor: bg,
    excludeBkgColor: sunk,
    gridColor: line,
    taskBkgColor: at(0),
    taskBorderColor: mix(at(0), fg, 70),
    taskTextColor: on(at(0)),
    taskTextLightColor: on(at(0)),
    taskTextOutsideColor: fg,
    taskTextDarkColor: fg,
    taskTextClickableColor: fg,
    activeTaskBkgColor: mix(at(0), bg, 45),
    activeTaskBorderColor: at(0),
    doneTaskBkgColor: line2,
    doneTaskBorderColor: fg3,
    critBkgColor: mix(del, bg, 40),
    critBorderColor: del,
    todayLineColor: fg2,
    vertLineColor: fg3,
    // Git graph
    commitLabelColor: fg,
    commitLabelBackground: sunk,
    tagLabelColor: fg,
    tagLabelBackground: sunk,
    tagLabelBorder: line2,
    // Class, state and ER diagrams
    classText: fg,
    stateBkg: sunk,
    stateLabelColor: fg,
    labelBackgroundColor: bg,
    compositeBackground: bg,
    compositeTitleBackground: sunk,
    compositeBorder: line2,
    altBackground: sunk,
    transitionColor: fg3,
    transitionLabelColor: fg2,
    specialStateColor: fg,
    innerEndBackground: fg,
    attributeBackgroundColorOdd: bg,
    attributeBackgroundColorEven: sunk,
    relationColor: fg3,
    relationLabelBackground: bg,
    relationLabelColor: fg,
    requirementBackground: sunk,
    requirementBorderColor: line2,
    requirementTextColor: fg,
    errorBkgColor: sunk,
    errorTextColor: fg,
  };
}

/** What Mermaid draws without its theme. Sankey's nodes are d3's Tableau 10, which are swapped for
 * the data colours, and its links blend by "multiply", which drowns them on a dark background. Some
 * lines (timeline, journey) and arrowheads are plain black, the timeline's take the colour of its
 * labels: they take the line colour. */
const TABLEAU10 = ["#4e79a7", "#f28e2c", "#e15759", "#76b7b2", "#59a14f", "#edc949", "#af7aa1", "#ff9da7", "#9c755f", "#bab0ab"];
function restyle(svg: string, id: string, vars: ReturnType<typeof themeVariables>): string {
  // :where() keeps these below every rule of Mermaid's own.
  let css = `#${id} :where(line[stroke="black"]){stroke:${vars.lineColor}} #${id} :where(marker path:not([fill])){fill:${vars.lineColor}}`;
  // Journey writes on its coloured boxes in the text colour.
  if (svg.includes('aria-roledescription="journey"')) css += ` #${id} .label{color:${vars.scaleLabelColor}}`;
  if (svg.includes('aria-roledescription="timeline"')) css += ` #${id} .lineWrapper line{stroke:${vars.lineColor}}`;
  if (svg.includes('aria-roledescription="sankey"')) {
    const data = vars.xyChart.plotColorPalette.split(",");
    const colors = new Map(TABLEAU10.map((c, i) => [c, data[i % data.length]]));
    svg = svg.replace(/#[0-9a-f]{6}\b/gi, (c) => colors.get(c.toLowerCase()) ?? c).replace(/mix-blend-mode:\s*multiply/g, "mix-blend-mode: normal");
    css += ` #${id} .node-labels text{fill:${vars.textColor}}`;
  }
  return svg.replace("</style>", `${css}</style>`);
}

/** The SVG for the page and for the lightbox. On the page it fills the block's width, but shrinks
 * at most to MIN_SCALE (else its text gets too small): a wider one scrolls in its block. The
 * lightbox's copy has its own size and the design's background. */
const MIN_SCALE = 0.75;
function sized(svg: string, background: string): { inline: string; image: string } {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = doc.documentElement;
  const [, , w, h] = (root.getAttribute("viewBox") ?? "").split(/[\s,]+/).map(Number);
  if (!w || !h) return { inline: svg, image: svg };
  root.setAttribute("width", String(Math.ceil(w)));
  root.setAttribute("height", String(Math.ceil(h)));
  root.setAttribute("style", `background-color: ${background}`);
  const image = new XMLSerializer().serializeToString(root);
  root.setAttribute("style", `width: 100%; height: auto; max-width: ${Math.ceil(w)}px; min-width: ${Math.ceil(w * MIN_SCALE)}px`);
  return { inline: new XMLSerializer().serializeToString(root), image };
}

type State = { svg: { inline: string; image: string }; error?: undefined } | { svg?: undefined; error: string } | { svg?: undefined; error?: undefined };

/** A ```mermaid block drawn as a diagram in the app's design. Until it is drawn, and when its
 * syntax is broken, it stays a code block (`fallback`), then with the error below. `open`: the block
 * is still being written (streaming), so it is not drawn yet. */
export function MermaidBlock({ code, open, fallback }: { code: string; open: boolean; fallback: ReactNode }) {
  const { t } = useTranslation();
  const look = useLook();
  const anchor = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<State>({});
  const [zoom, setZoom] = useState(false);
  const [showCode, setShowCode] = useState(false);

  useEffect(() => {
    if (open || !anchor.current) return;
    let cancelled = false;
    const vars = themeVariables(anchor.current, look.dark);
    // A diagram that picks its own theme (%%{init}%% or front matter) is drawn on Mermaid's: its own
    // colours win. Its own theme variables, style, classDef and linkStyle win anyway.
    const settings = [...(code.match(/^\s*---\n[\s\S]*?\n---/) ?? []), ...(code.match(/%%\{[\s\S]*?\}%%/g) ?? [])].join("\n");
    const ownTheme = /\btheme["']?\s*:/.test(settings);
    const own = ownTheme || /\btheme(Variables|CSS)["']?\s*:/.test(settings);
    draw(async (mermaid) => {
      const config = {
        startOnLoad: false,
        securityLevel: "strict" as const,
        suppressErrorRendering: true,
        htmlLabels: false,
        // Gantt takes the page's width (it is drawn off-screen): a fixed one that suits a message.
        gantt: { useWidth: 800, fontSize: 12, sectionFontSize: 12, barHeight: 22, leftPadding: 90, axisFormat: "%b %d" },
        ...(ownTheme ? {} : { theme: "base" as const, themeVariables: vars }),
      };
      const key = JSON.stringify(config);
      if (configured !== key) {
        mermaid.initialize(config);
        configured = key;
      }
      const id = `mermaid-${++counter}`;
      const { svg } = await mermaid.render(id, code);
      return own ? svg : restyle(svg, id, vars);
    })
      .then((svg) => {
        if (!cancelled) setState({ svg: sized(svg, vars.background) });
      })
      .catch((e: unknown) => {
        // A parse error: "Parse error on line 3:", the line, a marker, "Expecting …, got …".
        const lines = (e instanceof Error ? e.message : String(e)).split("\n").filter((l) => l.trim());
        const message = lines.length > 1 ? `${lines[0]} ${lines[lines.length - 1]}` : (lines[0] ?? "");
        if (!cancelled) setState({ error: message });
      });
    return () => {
      cancelled = true;
    };
  }, [code, open, look.design, look.dark]);

  const toggle = (
    <button type="button" onClick={() => setShowCode((v) => !v)} className="text-xs text-fg-3 hover:text-fg">
      {t(showCode ? "markdown.showDiagram" : "markdown.showCode")}
    </button>
  );

  if (open || !state.svg) {
    return (
      <div ref={anchor}>
        {fallback}
        {!open && state.error && (
          <p className="-mt-1 mb-2 line-clamp-2 text-xs text-del" role="status" title={state.error}>
            {t("markdown.diagramError", { error: state.error })}
          </p>
        )}
      </div>
    );
  }
  if (showCode) {
    return (
      <div ref={anchor} className="my-2">
        {fallback}
        {toggle}
      </div>
    );
  }
  return (
    <div ref={anchor} className="my-2">
      <button
        type="button"
        title={t("markdown.enlargeDiagram")}
        aria-label={t("markdown.enlargeDiagram")}
        onClick={() => setZoom(true)}
        className="scroll-thin block w-full cursor-zoom-in overflow-x-auto rounded-sm bg-bg p-2 ring-1 ring-line ring-inset"
      >
        <div dangerouslySetInnerHTML={{ __html: state.svg.inline }} />
      </button>
      {toggle}
      {zoom && (
        <Lightbox
          images={[{ src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(state.svg.image)}`, alt: t("markdown.diagram") }]}
          onClose={() => setZoom(false)}
        />
      )}
    </div>
  );
}
