import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronLeft, ExternalLink, FileText, Languages, Plus, Scale, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { DocCustomPage, DocSite } from "../../api/types";
import { Button, ErrorText, Field, IconButton, Input, Modal, Segmented, Spinner } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { DocMarkdown } from "./DocMarkdown";
import { languageLabel, MarkdownArea, Toolbar } from "./PageEditor";
import { base, siteHref, useDocCustomPages, useDocsRefresh } from "./queries";

/** A doc site's own pages outside its docs tree (#121): imprint, privacy policy, any page its footer links. Written
 * here by people only, never by the doc agent; left out of the site's search and its assistant. */
export function CustomPages({ pid, site }: { pid: number; site: DocSite }) {
  const { t } = useTranslation();
  const list = useDocCustomPages(pid, site.id);
  const refresh = useDocsRefresh(pid);
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const pages = list.data ?? [];
  const page = pages.find((p) => p.id === open);

  if (page) return <CustomEditor key={page.id} pid={pid} site={site} page={page} onBack={() => setOpen(null)} />;

  const create = async (kind: string, title: string) => {
    setBusy(kind);
    setError(null);
    try {
      const made = await api.post<DocCustomPage>(`${base(pid)}/sites/${site.id}/custom-pages`, { title });
      await refresh();
      setOpen(made.id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const move = async (p: DocCustomPage, position: number) => {
    await api.patch(`${base(pid)}/custom-pages/${p.id}`, { position }).catch((e: unknown) => setError(e));
    await refresh();
  };
  const has = (title: string) => pages.some((p) => p.title.toLowerCase() === title.toLowerCase());
  const imprint = t("docs.custom.imprint");
  const privacy = t("docs.custom.privacy");

  return (
    <div className="space-y-4">
      {list.isLoading ? (
        <Spinner />
      ) : pages.length === 0 ? (
        <div className="rounded-sm p-4 text-fg-2 ring-1 ring-line ring-inset">{t("docs.custom.empty")}</div>
      ) : (
        <ol data-part="custom-pages" className="divide-y divide-line rounded-sm ring-1 ring-line ring-inset">
          {pages.map((p, i) => (
            <li key={p.id} className="flex items-center gap-1 py-1 pr-1 pl-3">
              <button type="button" onClick={() => setOpen(p.id)} className="flex min-w-0 flex-1 items-center gap-2.5 py-1.5 text-left hover:text-fg">
                <FileText className="size-4 shrink-0 text-fg-3" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{p.title}</span>
                  <span className="flex min-w-0 flex-wrap items-center gap-x-2 text-fg-3">
                    <span className="truncate font-mono">/{p.path}</span>
                    {site.languages.length > 0 && (
                      <span className="inline-flex items-center gap-1">
                        <Languages className="size-3.5" />
                        {[site.language, ...site.languages].filter((l) => l === site.language || p.texts[l]?.body.trim()).join(" · ")}
                      </span>
                    )}
                  </span>
                </span>
              </button>
              <IconButton label={t("docs.custom.up")} disabled={i === 0} onClick={() => move(p, i - 1)}>
                <ArrowUp className="size-4" />
              </IconButton>
              <IconButton label={t("docs.custom.down")} disabled={i === pages.length - 1} onClick={() => move(p, i + 1)}>
                <ArrowDown className="size-4" />
              </IconButton>
            </li>
          ))}
        </ol>
      )}
      <div className="flex flex-wrap gap-2">
        {!has(imprint) && (
          <Button icon={<Scale className="size-4" />} loading={busy === "imprint"} onClick={() => create("imprint", imprint)}>
            {t("docs.custom.addImprint")}
          </Button>
        )}
        {!has(privacy) && (
          <Button icon={<ShieldCheck className="size-4" />} loading={busy === "privacy"} onClick={() => create("privacy", privacy)}>
            {t("docs.custom.addPrivacy")}
          </Button>
        )}
        <Button icon={<Plus className="size-4" />} loading={busy === "page"} onClick={() => create("page", t("docs.custom.newTitle"))}>
          {t("docs.custom.add")}
        </Button>
      </div>
      <ErrorText error={error} />
      <p className="text-fg-3">{t("docs.custom.rules")}</p>
    </div>
  );
}

type Texts = Record<string, { title: string; body: string }>;

function CustomEditor({ pid, site, page, onBack }: { pid: number; site: DocSite; page: DocCustomPage; onBack: () => void }) {
  const { t } = useTranslation();
  const refresh = useDocsRefresh(pid);
  const [lang, setLang] = useState(site.language);
  const [title, setTitle] = useState(page.title);
  const [slug, setSlug] = useState(page.slug);
  const [body, setBody] = useState(page.body);
  const [texts, setTexts] = useState<Texts>(page.texts);
  const [mode, setMode] = useState<"write" | "preview">("write");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [deleting, setDeleting] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const original = lang === site.language;
  const current = original ? { title, body } : (texts[lang] ?? { title: "", body: "" });
  const setCurrent = (patch: Partial<{ title: string; body: string }>) => {
    if (original) {
      if (patch.title !== undefined) setTitle(patch.title);
      if (patch.body !== undefined) setBody(patch.body);
    } else setTexts((old) => ({ ...old, [lang]: { ...(old[lang] ?? { title: "", body: "" }), ...patch } }));
  };
  const setCurrentBody = (v: string) => setCurrent({ body: v });
  const clean = (x: Texts) => JSON.stringify(Object.fromEntries(Object.entries(x).filter(([, v]) => v.title.trim() || v.body.trim())));
  const dirty = title !== page.title || slug !== page.slug || body !== page.body || clean(texts) !== clean(page.texts);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.patch(`${base(pid)}/custom-pages/${page.id}`, { title: title.trim() || page.title, slug, body, texts });
      await refresh();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" icon={<ChevronLeft className="size-4" />} onClick={onBack}>
          {t("docs.custom.all")}
        </Button>
        <div className="flex-1" />
        {site.published && (
          <a href={`${siteHref(site)}${page.path}`} target="_blank" rel="noreferrer" title={t("docs.page.onSite")} aria-label={t("docs.page.onSite")} className="inline-flex size-8 items-center justify-center rounded-sm text-fg-3 hover:bg-sunk hover:text-fg touch:size-11">
            <ExternalLink className="size-4" />
          </a>
        )}
        <IconButton label={t("common.delete")} onClick={() => setDeleting(true)}>
          <Trash2 className="size-4" />
        </IconButton>
      </div>

      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_14rem]">
        <Field label={original ? t("docs.custom.title") : t("docs.custom.titleIn", { language: languageLabel(lang) })}>
          <Input value={current.title} onChange={(e) => setCurrent({ title: e.target.value })} placeholder={original ? "" : title} maxLength={200} />
        </Field>
        <Field label={t("docs.custom.address")} hint={`/p/${slug}`}>
          <Input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} maxLength={120} />
        </Field>
      </div>

      {site.languages.length > 0 && (
        <div className="space-y-1.5">
          <Segmented
            value={lang}
            onChange={setLang}
            aria-label={t("docs.custom.language")}
            className="max-w-full overflow-x-auto"
            options={[site.language, ...site.languages].map((l) => ({
              value: l,
              label: (
                <span className="flex items-center gap-1.5">
                  {languageLabel(l)}
                  {l !== site.language && !texts[l]?.body.trim() && <span aria-label={t("docs.custom.noText")} className="size-1.5 rounded-full bg-you-fg" />}
                </span>
              ),
            }))}
          />
          {!original && <p className="text-fg-3">{t("docs.custom.byHand", { language: languageLabel(site.language) })}</p>}
        </div>
      )}

      <div className="overflow-hidden rounded-sm ring-1 ring-line-2 ring-inset">
        <div className="flex items-center gap-2 border-b border-line px-2 py-1">
          {mode === "write" && <Toolbar pid={pid} site={site} area={area} setBody={setCurrentBody} />}
          <div className="flex-1" />
          <Segmented
            value={mode}
            onChange={setMode}
            aria-label={t("docs.page.mode")}
            options={[
              { value: "write", label: t("docs.page.write") },
              { value: "preview", label: t("docs.page.preview") },
            ]}
          />
        </div>
        <div className={clsx("max-h-[60vh] overflow-y-auto", mode === "write" && "[&_textarea]:min-h-64")}>
          {mode === "write" ? (
            <MarkdownArea key={lang} pid={pid} site={site} area={area} value={current.body} onChange={setCurrentBody} />
          ) : (
            <div className="min-h-64 px-4 py-4 sm:px-5">{current.body.trim() ? <DocMarkdown text={current.body} /> : <p className="text-fg-3">{original ? t("docs.page.emptyBody") : t("docs.custom.noText")}</p>}</div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-fg-3">{t("docs.page.edited", { who: page.edited_by || "–", when: timeAgo(page.updated_at) })}</span>
        <ErrorText error={error} />
        {dirty && (
          <Button
            variant="ghost"
            onClick={() => {
              setTitle(page.title);
              setSlug(page.slug);
              setBody(page.body);
              setTexts(page.texts);
            }}
          >
            {t("docs.page.discard")}
          </Button>
        )}
        <Button variant="primary" onClick={save} loading={saving} disabled={!dirty || !slug}>
          {t("common.save")}
        </Button>
      </div>

      <Modal
        open={deleting}
        onClose={() => setDeleting(false)}
        width="max-w-md"
        title={t("docs.custom.deleteTitle", { title: page.title })}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="danger"
              onClick={async () => {
                await api.del(`${base(pid)}/custom-pages/${page.id}`);
                setDeleting(false);
                onBack();
                await refresh();
              }}
            >
              {t("common.delete")}
            </Button>
          </>
        }
      >
        <p>{t("docs.custom.deleteText")}</p>
      </Modal>
    </div>
  );
}
