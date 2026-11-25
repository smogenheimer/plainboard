import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { DocApiKey, DocSite } from "../../api/types";
import { CodeBlock, CopyButton } from "../../components/Markdown";
import { Button, ErrorText, Field, IconButton, Input, Select } from "../../components/ui";
import { formatDateTime, timeAgo } from "../../lib/format";
import { base } from "./queries";

const EXPIRY = [0, 30, 90, 365];
/** The API's endpoints below the site's base address, each with the i18n key of its description. */
const ENDPOINTS: [string, string][] = [
  ["", "site"],
  ["/pages?cursor=0&limit=500", "pages"],
  ["/pages/{id}", "page"],
  ["/page?path=…", "pageByPath"],
  ["/footer", "footer"],
  ["/files/{id}", "file"],
  ["/export?format=jsonl", "exportJsonl"],
  ["/export?format=zip&since=…", "exportZip"],
];
const PARAMS = ["version", "lang", "since", "cursor"];
const LIST = "divide-y divide-line rounded-sm ring-1 ring-line ring-inset";

/** A doc site's read-only API (#131): its keys (shown once, with an optional expiry) and what the API offers. */
export function DocApi({ pid, site }: { pid: number; site: DocSite }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const path = `${base(pid)}/sites/${site.id}/api-keys`;
  const queryKey = ["docs", pid, "api-keys", site.id];
  const keys = useQuery({ queryKey, queryFn: () => api.get<DocApiKey[]>(path) });
  const [name, setName] = useState("");
  const [days, setDays] = useState(0);
  const [created, setCreated] = useState<DocApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const url = `${window.location.origin}/api/v1/docs/${site.slug}`;
  const curl = `curl -H "Authorization: Bearer pbd_…" \\\n  "${url}/export?format=zip" -o ${site.slug}.zip`;

  const change = async (action: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await action();
      await qc.invalidateQueries({ queryKey });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const create = () =>
    change(async () => {
      const expires_at = days ? new Date(Date.now() + days * 86400000).toISOString() : null;
      setCreated(await api.post<DocApiKey>(path, { name: name.trim(), expires_at }));
      setCopied(false);
      setName("");
    });
  const revoke = (key: DocApiKey) => {
    if (!confirm(t("docs.api.confirmRevoke", { name: key.name }))) return;
    change(async () => {
      await api.del(`${path}/${key.id}`);
      if (created?.id === key.id) setCreated(null);
    });
  };

  return (
    <div className="space-y-5">
      <Field label={t("docs.api.keys")} hint={t("docs.api.keysHint")} group>
        {(keys.data?.length ?? 0) > 0 && (
          <ul data-part="doc-api-keys" className={LIST}>
            {keys.data!.map((key) => (
              <li key={key.id} className="flex items-center gap-2 px-3 py-2">
                <KeyRound className="size-3.5 shrink-0 text-fg-3" />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">
                    {key.name}
                    <span className="ml-1.5 font-mono font-normal text-fg-3">…{key.hint}</span>
                  </div>
                  <div className="truncate text-fg-3">
                    {t("docs.api.created", { when: timeAgo(key.created_at), who: key.created_by || "–" })} ·{" "}
                    {key.expires_at ? t("docs.api.expires", { when: formatDateTime(key.expires_at) }) : t("docs.api.noExpiry")} ·{" "}
                    {key.last_used_at ? t("docs.api.used", { when: timeAgo(key.last_used_at) }) : t("docs.api.neverUsed")}
                  </div>
                </div>
                <IconButton label={t("docs.api.revoke")} onClick={() => revoke(key)}>
                  <Trash2 className="size-3.5" />
                </IconButton>
              </li>
            ))}
          </ul>
        )}
        {created?.key && (
          <div className="space-y-1 rounded-sm bg-you-bg p-3">
            <p className="text-you-fg">{t("docs.api.copy")}</p>
            <div className="flex gap-2">
              <Input readOnly value={created.key} onFocus={(e) => e.target.select()} className="font-mono" />
              <Button
                size="md"
                onClick={async () => {
                  await navigator.clipboard?.writeText(created.key!).catch(() => undefined);
                  setCopied(true);
                }}
              >
                {copied ? t("docs.api.copied") : t("common.copy")}
              </Button>
            </div>
          </div>
        )}
        <div className="grid gap-2 sm:grid-cols-[1fr_11rem_auto]">
          <Input value={name} maxLength={200} placeholder={t("docs.api.namePlaceholder")} onChange={(e) => setName(e.target.value)} />
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label={t("docs.api.expiry")}>
            {EXPIRY.map((d) => (
              <option key={d} value={d}>
                {d ? t("docs.api.days", { count: d }) : t("docs.api.never")}
              </option>
            ))}
          </Select>
          <Button variant="primary" size="md" disabled={!name.trim()} loading={busy} onClick={create}>
            {t("docs.api.create")}
          </Button>
        </div>
        <ErrorText error={error} />
      </Field>
      <Field label={t("docs.api.endpoints")} hint={t("docs.api.endpointsHint")} group>
        <div data-part="doc-api-base" className="flex items-center gap-2 rounded-sm bg-sunk py-1 pr-1 pl-3">
          <span className="shrink-0 text-fg-3">{t("docs.api.base")}</span>
          <code className="min-w-0 flex-1 truncate font-mono" title={url}>
            {url}
          </code>
          <CopyButton text={url} />
        </div>
        <ul data-part="doc-api-endpoints" className={LIST}>
          {ENDPOINTS.map(([path, key]) => (
            <li key={key} className="flex items-start gap-2 py-1.5 pr-1 pl-3">
              <span className="mt-0.5 shrink-0 rounded-sm px-1 font-mono text-[11px] leading-4 font-semibold text-fg-3 ring-1 ring-line-2 ring-inset">GET</span>
              <div className="min-w-0 flex-1">
                <code className="block font-mono break-words text-fg">{path || "/"}</code>
                <div className="text-fg-3">{t(`docs.api.endpoint.${key}`)}</div>
              </div>
              <CopyButton text={url + path} />
            </li>
          ))}
        </ul>
      </Field>
      <Field label={t("docs.api.params")} hint={t("docs.api.paramsHint")} group>
        <dl data-part="doc-api-params" className={LIST}>
          {PARAMS.map((p) => (
            <div key={p} className="grid gap-x-3 px-3 py-1.5 sm:grid-cols-[7rem_1fr]">
              <dt className="font-mono text-fg">{t(`docs.api.param.${p}.name`)}</dt>
              <dd className="text-fg-3">{t(`docs.api.param.${p}.text`)}</dd>
            </div>
          ))}
        </dl>
      </Field>
      <Field label={t("docs.api.example")} hint={t("docs.api.exampleHint")} group>
        <div className="prose-chat">
          <CodeBlock alwaysCopy text={curl}>
            <code>{curl}</code>
          </CodeBlock>
        </div>
      </Field>
    </div>
  );
}
