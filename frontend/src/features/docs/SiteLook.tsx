import clsx from "clsx";
import { ExternalLink, ImagePlus, Loader2, Moon, Plus, RotateCcw, Search, Sun, Trash2, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import type { DocSite, DocTheme, DocThemeColors } from "../../api/types";
import { SaveRow } from "../../components/SettingsLayout";
import { Button, Field, IconButton, Input, Segmented, Select, Textarea, Toggle } from "../../components/ui";
import { upload } from "./PageEditor";
import { base, siteHref, useDocsRefresh } from "./queries";
import "./theme-preview.css";

type Mode = "light" | "dark";

const NO_COLORS: DocThemeColors = { accent: "", page: "", surface: "", text: "" };
export const DEFAULT_THEME: DocTheme = {
  logo_light: null,
  logo_dark: null,
  favicon: null,
  show_name: true,
  light: NO_COLORS,
  dark: NO_COLORS,
  font_family: "",
  font_url: "",
  radius: null,
  links: [],
  css: "",
};

/** What the site wears without a theme (designs/slop2gether.css): shown where a colour is not set. */
const SITE_COLORS: Record<Mode, DocThemeColors> = {
  light: { accent: "#6741d9", page: "#fbfbfd", surface: "#ffffff", text: "#1d1d1f" },
  dark: { accent: "#7c4dff", page: "#000000", surface: "#0b0b0d", text: "#f5f5f7" },
};

const FONTS: { id: string; family: string }[] = [
  { id: "default", family: "" },
  { id: "system", family: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif" },
  { id: "serif", family: "Georgia, Cambria, Times New Roman, serif" },
  { id: "mono", family: "ui-monospace, SFMono-Regular, Menlo, monospace" },
];

const RADII: { value: string; radius: number | null }[] = [
  { value: "0", radius: 0 },
  { value: "6", radius: 6 },
  { value: "", radius: null },
  { value: "24", radius: 24 },
];

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** A doc site's own look (#121): logos, colours for light and dark, font, corners, links in its header and custom
 * CSS, with a live preview of the site as it will look. Nothing set: the site looks as every site does. */
export function SiteLook({ pid, site }: { pid: number; site: DocSite }) {
  const { t } = useTranslation();
  const refresh = useDocsRefresh(pid);
  const saved = { ...DEFAULT_THEME, ...site.theme };
  const [theme, setTheme] = useState<DocTheme>(saved);
  const [mode, setMode] = useState<Mode>(() => (document.documentElement.dataset.theme?.endsWith("dark") ? "dark" : "light"));
  const preview = usePreviewCss(pid, site, theme);
  const set = (patch: Partial<DocTheme>) => setTheme((old) => ({ ...old, ...patch }));
  const setColor = (m: Mode, key: keyof DocThemeColors, value: string) => setTheme((old) => ({ ...old, [m]: { ...old[m], [key]: value } }));
  const isDefault = same(theme, DEFAULT_THEME);
  const validColors = (["light", "dark"] as Mode[]).every((m) => Object.values(theme[m]).every((v) => v === "" || HEX.test(v)));
  const validLinks = theme.links.every((l) => l.label.trim() && l.url.trim());
  const font = theme.font_url || !FONTS.some((f) => f.family === theme.font_family) ? "custom" : (FONTS.find((f) => f.family === theme.font_family)?.id ?? "default");

  return (
    <div className="space-y-6">
      <div data-part="theme-preview" className="space-y-2 bg-bg lg:sticky lg:top-0 lg:z-10 lg:-mx-2 lg:px-2 lg:pt-3 lg:pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-fg-2">{t("docs.look.preview")}</span>
          {isDefault && <span className="text-fg-3">· {t("docs.look.isDefault")}</span>}
          <div className="flex-1" />
          <Segmented
            value={mode}
            onChange={setMode}
            aria-label={t("docs.look.mode")}
            options={[
              { value: "light", label: <Sun className="size-4" aria-label={t("docs.look.light")} /> },
              { value: "dark", label: <Moon className="size-4" aria-label={t("docs.look.dark")} /> },
            ]}
          />
        </div>
        <Preview site={site} theme={theme} mode={mode} css={preview.css} />
        {preview.error && <p className="text-you-fg">{preview.error}</p>}
      </div>

      <Field group label={t("docs.look.logo")} hint={t("docs.look.logoHint")}>
        <div className="grid gap-2 sm:grid-cols-3">
          <ImageSlot pid={pid} site={site} label={t("docs.look.logoLight")} value={theme.logo_light} onChange={(id) => set({ logo_light: id })} dark={false} />
          <ImageSlot pid={pid} site={site} label={t("docs.look.logoDark")} value={theme.logo_dark} onChange={(id) => set({ logo_dark: id })} dark empty={t("docs.look.logoDarkEmpty")} />
          <ImageSlot pid={pid} site={site} label={t("docs.look.favicon")} value={theme.favicon} onChange={(id) => set({ favicon: id })} dark={false} small />
        </div>
        {theme.logo_light !== null && <Toggle checked={theme.show_name} onChange={(v) => set({ show_name: v })} label={t("docs.look.showName", { name: site.name })} />}
      </Field>

      <Field group label={t("docs.look.colors")} hint={t("docs.look.colorsHint")}>
        <div className="grid gap-4 sm:grid-cols-2">
          {(["light", "dark"] as Mode[]).map((m) => (
            <fieldset key={m} className="min-w-0 space-y-1.5 rounded-sm p-3 ring-1 ring-line ring-inset">
              <legend className="flex items-center gap-1.5 px-1 font-medium">
                {m === "light" ? <Sun className="size-4" /> : <Moon className="size-4" />} {t(`docs.look.${m}`)}
              </legend>
              {(["accent", "page", "surface", "text"] as (keyof DocThemeColors)[]).map((key) => (
                <ColorRow key={key} label={t(`docs.look.color.${key}`)} value={theme[m][key]} fallback={SITE_COLORS[m][key]} onChange={(v) => setColor(m, key, v)} onFocus={() => setMode(m)} />
              ))}
            </fieldset>
          ))}
        </div>
      </Field>

      <Field group label={t("docs.look.font")} hint={t("docs.look.fontHint")}>
        <Select
          value={font}
          aria-label={t("docs.look.font")}
          onChange={(e) => {
            const id = e.target.value;
            if (id !== "custom") set({ font_family: FONTS.find((f) => f.id === id)?.family ?? "", font_url: "" });
            else if (font !== "custom") set({ font_family: "Source Sans 3, sans-serif", font_url: "" });
          }}
        >
          {FONTS.map((f) => (
            <option key={f.id} value={f.id}>
              {t(`docs.look.fonts.${f.id}`)}
            </option>
          ))}
          <option value="custom">{t("docs.look.fonts.custom")}</option>
        </Select>
        {font === "custom" && (
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="space-y-1">
              <span className="block text-fg-2">{t("docs.look.fontFamily")}</span>
              <Input value={theme.font_family} onChange={(e) => set({ font_family: e.target.value })} placeholder="Source Sans 3, sans-serif" />
            </label>
            <label className="space-y-1">
              <span className="block text-fg-2">{t("docs.look.fontUrl")}</span>
              <Input value={theme.font_url} onChange={(e) => set({ font_url: e.target.value.trim() })} placeholder="https://fonts.googleapis.com/css2?family=…" inputMode="url" />
            </label>
          </div>
        )}
      </Field>

      <Field group label={t("docs.look.corners")} hint={t("docs.look.cornersHint")}>
        <Segmented
          value={theme.radius === null ? "" : String(theme.radius)}
          onChange={(v) => set({ radius: RADII.find((r) => r.value === v)?.radius ?? null })}
          aria-label={t("docs.look.corners")}
          options={RADII.map((r) => ({ value: r.value, label: <Corner radius={r.radius} label={t(`docs.look.radius.${r.value || "default"}`)} /> }))}
        />
      </Field>

      <Field group label={t("docs.look.links")} hint={t("docs.look.linksHint")}>
        {theme.links.map((link, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={link.label}
              maxLength={40}
              onChange={(e) => set({ links: theme.links.map((l, k) => (k === i ? { ...l, label: e.target.value } : l)) })}
              placeholder={t("docs.look.linkLabel")}
              aria-label={t("docs.look.linkLabel")}
              className="w-32 shrink-0 sm:w-40"
            />
            <Input
              value={link.url}
              onChange={(e) => set({ links: theme.links.map((l, k) => (k === i ? { ...l, url: e.target.value.trim() } : l)) })}
              placeholder="https://example.com"
              aria-label={t("docs.look.linkUrl")}
              inputMode="url"
              className="min-w-0 flex-1"
            />
            <IconButton label={t("common.remove")} onClick={() => set({ links: theme.links.filter((_, k) => k !== i) })}>
              <X className="size-4" />
            </IconButton>
          </div>
        ))}
        {theme.links.length < 6 && (
          <Button size="sm" icon={<Plus className="size-4" />} onClick={() => set({ links: [...theme.links, { label: theme.links.length ? "" : t("docs.look.linkWebsite"), url: "" }] })}>
            {t("docs.look.addLink")}
          </Button>
        )}
      </Field>

      <details className="group" open={theme.css ? true : undefined}>
        <summary className="cursor-pointer font-medium text-fg-2 hover:text-fg">{t("docs.look.advanced")}</summary>
        <div className="mt-3">
          <Field label={t("docs.look.css")} hint={t("docs.look.cssHint")}>
            <Textarea value={theme.css} onChange={(e) => set({ css: e.target.value })} rows={6} spellCheck={false} placeholder={".doc-header {\n  border-bottom-width: 2px;\n}"} className="font-mono" />
          </Field>
        </div>
      </details>

      <SaveRow
        dirty={!same(theme, saved) && validColors && validLinks}
        onSave={async () => {
          await api.patch(`${base(pid)}/sites/${site.id}`, { theme });
          await refresh();
        }}
      >
        {!isDefault && (
          <Button variant="ghost" icon={<RotateCcw className="size-4" />} onClick={() => setTheme(DEFAULT_THEME)}>
            {t("docs.look.reset")}
          </Button>
        )}
        {site.published && (
          <a href={siteHref(site)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-fg-2 hover:text-fg">
            <ExternalLink className="size-4" /> {t("docs.openSite")}
          </a>
        )}
      </SaveRow>
    </div>
  );
}

/** The theme's CSS for the preview, as the site gets it (made by the server, which also checks it). */
function usePreviewCss(pid: number, site: DocSite, theme: DocTheme): { css: string; error: string } {
  const [state, setState] = useState({ css: "", error: "" });
  const key = JSON.stringify({ ...theme, css: "", links: [], font_url: "" }); // what the CSS is made of
  useEffect(() => {
    let gone = false;
    const timer = window.setTimeout(() => {
      api
        .post<{ css: string }>(`${base(pid)}/sites/${site.id}/theme/preview`, JSON.parse(key) as Record<string, unknown>)
        .then((r) => !gone && setState({ css: r.css, error: "" }))
        .catch((e: unknown) => !gone && setState((s) => ({ ...s, error: errorMessage(e) })));
    }, 180);
    return () => {
      gone = true;
      window.clearTimeout(timer);
    };
  }, [pid, site.id, key]);
  return state;
}

/** The site in small: its header, the tree, a page and the footer, in the theme's colours, font and corners. */
function Preview({ site, theme, mode, css }: { site: DocSite; theme: DocTheme; mode: Mode; css: string }) {
  const { t } = useTranslation();
  const logo = mode === "dark" ? (theme.logo_dark ?? theme.logo_light) : theme.logo_light;
  useWebFont(theme.font_url);
  return (
    <div className="doc-tp-frame">
      <style>{css}</style>
      <div className="doc-tp" data-design="slop2gether" data-theme={`slop2gether-${mode}`} aria-hidden>
        <div className="tp-head">
          {logo !== null ? <img src={`/api/attachments/${logo}`} alt="" className="tp-logo" /> : <span className="tp-mark">{site.name.charAt(0).toUpperCase()}</span>}
          {(logo === null || theme.show_name) && <span className="tp-name">{site.name}</span>}
          <span className="tp-grow" />
          {theme.links.slice(0, 2).map((l, i) => (
            <span key={i} className="tp-link">
              {l.label || "…"}
            </span>
          ))}
          <span className="tp-search">
            <Search className="size-3" /> {t("docs.look.sample.search")}
          </span>
          {mode === "dark" ? <Sun className="tp-icon size-3.5" /> : <Moon className="tp-icon size-3.5" />}
        </div>
        <div className="tp-body">
          <div className="tp-side">
            <span>{t("docs.look.sample.intro")}</span>
            <span className="is-on">{t("docs.look.sample.start")}</span>
            <span>{t("docs.look.sample.guides")}</span>
            <span>{t("docs.look.sample.api")}</span>
          </div>
          <div className="tp-main">
            <span className="tp-crumbs">
              {site.name} › {t("docs.look.sample.guides")}
            </span>
            <span className="tp-title">{t("docs.look.sample.start")}</span>
            <span className="tp-text">
              {t("docs.look.sample.text")} <span className="tp-a">{t("docs.look.sample.link")}</span>.
            </span>
            <span className="tp-card">
              <span className="tp-code">npm install</span>
              <span className="tp-btn">{t("docs.look.sample.next")} →</span>
            </span>
            <span className="tp-foot">
              <span>{t("docs.look.sample.imprint")}</span>
              <span>{t("docs.look.sample.privacy")}</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The web font's stylesheet in the page while the preview shows it. */
function useWebFont(url: string) {
  useEffect(() => {
    if (!/^https:\/\/\S+$/.test(url)) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    document.head.appendChild(link);
    return () => link.remove();
  }, [url]);
}

function Corner({ radius, label }: { radius: number | null; label: string }) {
  const r = radius ?? 14;
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden className="size-3.5 border-t-2 border-l-2 border-current" style={{ borderTopLeftRadius: Math.min(r, 14) * 0.6 }} />
      {label}
    </span>
  );
}

function ColorRow({ label, value, fallback, onChange, onFocus }: { label: string; value: string; fallback: string; onChange: (v: string) => void; onFocus: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const bad = text !== "" && !HEX.test(text);
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="min-w-0 flex-1 truncate text-fg-2">
        {label}
      </label>
      <span className="relative inline-flex size-8 shrink-0 overflow-hidden rounded-sm ring-1 ring-line-2 ring-inset touch:size-11" style={{ background: value || fallback }}>
        <input
          type="color"
          value={(value || fallback).slice(0, 7)}
          onFocus={onFocus}
          onChange={(e) => onChange(e.target.value)}
          aria-label={label}
          className="absolute inset-0 size-full cursor-pointer opacity-0"
        />
      </span>
      <div className="w-24 shrink-0">
      <Input
        id={id}
        value={text}
        onFocus={onFocus}
        onChange={(e) => {
          const v = e.target.value.trim();
          setText(v);
          if (v === "" || HEX.test(v)) onChange(v);
        }}
        placeholder={fallback}
        aria-invalid={bad || undefined}
        className={clsx("font-mono", bad && "ring-you")}
        maxLength={9}
      />
      </div>
      <IconButton label={t("docs.look.useDefault")} onClick={() => onChange("")} disabled={!value} className={clsx(!value && "invisible")}>
        <X className="size-3.5" />
      </IconButton>
    </div>
  );
}

/** A picture of the site (a logo, the favicon): uploaded into its files, which keeps it. */
function ImageSlot({ pid, site, label, value, onChange, dark, empty, small }: { pid: number; site: DocSite; label: string; value: number | null; onChange: (id: number | null) => void; dark: boolean; empty?: string; small?: boolean }) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pick = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const [done] = await upload(pid, site, [file]);
      onChange(done.id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  let content: ReactNode;
  if (busy) content = <Loader2 className="size-5 animate-spin text-fg-3" />;
  else if (value !== null) content = <img src={`/api/attachments/${value}`} alt={label} className={clsx("max-w-full object-contain", small ? "size-8" : "max-h-10")} />;
  else
    content = (
      <span className="flex flex-col items-center gap-1 px-2 text-center text-fg-3">
        <ImagePlus className="size-5" />
        <span>{empty ?? t("docs.look.upload")}</span>
      </span>
    );
  return (
    <div className="min-w-0 space-y-1">
      <span className="block text-fg-2">{label}</span>
      <div className="relative">
        <button
          type="button"
          onClick={() => input.current?.click()}
          className={clsx(
            "flex h-20 w-full items-center justify-center rounded-sm ring-1 ring-line-2 ring-inset transition-colors hover:ring-fg-3",
            dark ? "bg-[#0b0b0d]" : "bg-[#fbfbfd]",
            value === null && "border border-dashed border-transparent",
          )}
          aria-label={value === null ? `${label}: ${t("docs.look.upload")}` : `${label}: ${t("docs.look.replace")}`}
        >
          {content}
        </button>
        {value !== null && (
          <button
            type="button"
            onClick={() => onChange(null)}
            aria-label={`${label}: ${t("common.remove")}`}
            title={t("common.remove")}
            className="absolute top-1 right-1 inline-flex size-7 items-center justify-center rounded-sm bg-bg/90 text-fg-2 ring-1 ring-line-2 hover:text-fg touch:size-9"
          >
            <Trash2 className="size-3.5" />
          </button>
        )}
      </div>
      <input ref={input} type="file" accept="image/*" hidden onChange={(e) => (void pick(e.target.files?.[0]), (e.target.value = ""))} />
      {error && <p className="text-you-fg">{error}</p>}
    </div>
  );
}
