import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import { checks, qk, useAccounts, useMe, useMeta, useSettings } from "../../api/queries";
import type { Account, CheckResult, ProviderInfo } from "../../api/types";
import { Button, ErrorText, Field, IconButton, Input, Select, Textarea, Toggle } from "../../components/ui";
import { Sharing, Step } from "./AccountsEditor";
import { doneCount, useAccountStatus } from "./AccountStatus";
import { CheckBadge } from "./SettingsDialog";

const lines = (text: string) => [...new Set(text.split("\n").map((l) => l.trim()).filter(Boolean))];

/** Accounts of the providers that are an address: a local model server, a model host, a gateway.
    Each holds where it is, its key and the models it offers. Saved right away; the key is write-only.
    ``own``: people's own ones, as in AccountsEditor (a non-admin adds their own here, an admin sees everyone's). */
export function EndpointAccounts({ own = false }: { own?: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const meta = useMeta();
  const me = useMe();
  const accounts = useAccounts();
  const [error, setError] = useState<unknown>(null);
  const [choice, setChoice] = useState("");
  const endpoints = meta.data?.endpoints ?? [];
  const allowed = useSettings().data?.values.own_accounts ?? true;
  const adds = !own || (!me?.is_admin && allowed);
  const mine = (accounts.data ?? []).filter((a) => (own ? a.owner_id != null && a.can_manage : a.owner_id == null) && endpoints.some((p) => p.name === a.provider));
  const chosen = endpoints.find((p) => p.name === choice) ?? endpoints[0];

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      // What an account offers decides which providers and models agents can be given.
      await Promise.all([qc.invalidateQueries({ queryKey: qk.accounts }), qc.invalidateQueries({ queryKey: qk.meta })]);
      return true;
    } catch (err) {
      setError(err);
      return false;
    }
  };

  if (!endpoints.length || (!adds && !mine.length)) return null;
  return (
    <div className="space-y-3">
      {mine.map((account) => (
        <EndpointCard key={account.id} account={account} provider={endpoints.find((p) => p.name === account.provider)!} onChange={run} />
      ))}
      {adds && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1 basis-48">
            <Select aria-label={t("endpoints.choose")} value={chosen?.name ?? ""} onChange={(e) => setChoice(e.target.value)}>
              {endpoints.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.label}
                </option>
              ))}
            </Select>
          </div>
          <Button size="md" icon={<Plus className="size-3.5" />} onClick={() => chosen && run(() => api.post("/accounts", { provider: chosen.name, auth_mode: "api_key" }))}>
            {t("endpoints.add")}
          </Button>
        </div>
      )}
      {adds && chosen?.endpoint?.hint && <p className="text-fg-3">{chosen.endpoint.hint}</p>}
      <ErrorText error={error} />
    </div>
  );
}

function EndpointCard({ account, provider, onChange }: { account: Account; provider: ProviderInfo; onChange: (action: () => Promise<unknown>) => Promise<boolean> }) {
  const { t } = useTranslation();
  const me = useMe();
  const [label, setLabel] = useState(account.label);
  const [url, setUrl] = useState(account.endpoint_url);
  const [models, setModels] = useState(account.models.join("\n"));
  const [context, setContext] = useState(account.context_tokens ? String(account.context_tokens) : "");
  const [secret, setSecret] = useState("");
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [offered, setOffered] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("");
  const [loadError, setLoadError] = useState<unknown>(null);
  useEffect(() => setLabel(account.label), [account.label]);
  useEffect(() => setUrl(account.endpoint_url), [account.endpoint_url]);
  useEffect(() => setModels(account.models.join("\n")), [account.models]);
  useEffect(() => setContext(account.context_tokens ? String(account.context_tokens) : ""), [account.context_tokens]);

  const endpoint = provider.endpoint!;
  const patch = (body: Record<string, unknown>) => onChange(() => api.patch(`/accounts/${account.id}`, body));
  const remove = () => confirm(t("endpoints.confirmDelete", { name: account.label })) && onChange(() => api.del(`/accounts/${account.id}`));
  const saveSecret = async () => {
    if (secret && (await patch({ secret }))) setSecret("");
  };
  const saveModels = (text: string) => {
    const next = lines(text);
    if (next.join("\n") !== account.models.join("\n")) patch({ models: next });
  };
  const named = lines(models);
  const toggle = (id: string) => {
    const next = named.includes(id) ? named.filter((m) => m !== id) : [...named, id];
    setModels(next.join("\n"));
    saveModels(next.join("\n"));
  };
  const load = async () => {
    setLoadError(null);
    setLoading(true);
    try {
      setOffered((await api.post<{ models: string[] }>(`/accounts/${account.id}/models`)).models);
    } catch (err) {
      setLoadError(err);
    } finally {
      setLoading(false);
    }
  };
  const test = async () => {
    const model = named[0] ?? provider.models[0]?.id;
    if (!model) return setCheck({ ok: false, detail: t("endpoints.testNeedsModel") });
    setCheck({ ok: true, detail: t("settings.testing") });
    try {
      setCheck(await checks.account(account.id, model));
    } catch (err) {
      setCheck({ ok: false, detail: errorMessage(err) });
    }
  };
  const shown = (offered ?? []).filter((id) => id.toLowerCase().includes(filter.trim().toLowerCase()));

  const status = useAccountStatus(account);

  const head = (
    <div className="flex items-center gap-2">
      <Input
        aria-label={t("accounts.label")}
        className="min-w-0 flex-1 font-medium"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        onBlur={() => label.trim() && label !== account.label && patch({ label })}
      />
      <Toggle checked={account.enabled} onChange={(enabled) => patch({ enabled })} label={<span>{t("accounts.enabled")}</span>} />
      <IconButton label={t("accounts.delete")} onClick={remove}>
        <Trash2 className="size-3.5" />
      </IconButton>
    </div>
  );
  const kind = (
    <p className="text-fg-3">
      {provider.label}
      {endpoint.hint && ` · ${endpoint.hint}`}
    </p>
  );
  const ownedBy = account.owner_id != null && account.owner_id !== me?.id && <p className="text-fg-3">{t("accounts.ownedBy", { name: account.owner_name })}</p>;
  const sharing = account.owner_id != null && <Sharing account={account} patch={patch} />;
  const connection = (
    <div className="space-y-3" data-part="endpoint-connection">
      <Field label={t("endpoints.address")} hint={endpoint.url ? t("endpoints.addressHint", { url: endpoint.url }) : t("endpoints.addressNeeded")}>
        <Input value={url} placeholder={endpoint.url || "https://"} onChange={(e) => setUrl(e.target.value)} onBlur={() => url.trim() !== account.endpoint_url && patch({ endpoint_url: url.trim() })} />
      </Field>
      <div className="space-y-1">
        <span className="block font-medium text-fg-2">{t(endpoint.key ? "endpoints.key" : "endpoints.keyOptional")}</span>
        <div className="flex gap-2">
          <Input type="password" autoComplete="off" placeholder="••••••••" value={secret} onChange={(e) => setSecret(e.target.value)} />
          <Button size="md" variant="primary" onClick={saveSecret} disabled={!secret}>
            {t("common.save")}
          </Button>
          {account.secret_set && (
            <Button size="md" variant="ghost" onClick={() => patch({ secret: "" })}>
              {t("settings.clear")}
            </Button>
          )}
        </div>
        <span className="block text-fg-3">{account.secret_set ? t("settings.secretSet") : endpoint.key ? t("settings.secretUnset") : t("endpoints.keyNotNeeded")}</span>
      </div>
      <Field label={t("endpoints.models")} hint={t(provider.models.length ? "endpoints.modelsHintUsual" : "endpoints.modelsHint")}>
        <Textarea
          rows={Math.min(8, Math.max(2, named.length + 1))}
          className="font-mono"
          value={models}
          placeholder={provider.models.map((m) => m.id).join("\n")}
          onChange={(e) => setModels(e.target.value)}
          onBlur={() => saveModels(models)}
        />
      </Field>
      <Field label={t("endpoints.context")} hint={t("endpoints.contextHint")}>
        <Input
          type="number"
          min={0}
          step={1000}
          className="sm:w-40"
          value={context}
          placeholder="128000"
          onChange={(e) => setContext(e.target.value)}
          onBlur={() => (Number(context) || 0) !== account.context_tokens && patch({ context_tokens: Math.max(0, Math.round(Number(context) || 0)) })}
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" loading={loading} onClick={load}>
          {t("endpoints.load")}
        </Button>
        <Button size="sm" onClick={test}>
          {t("settings.test")}
        </Button>
        <CheckBadge result={check} />
      </div>
      <ErrorText error={loadError} />
      {offered && (
        <div className="space-y-2 rounded-sm bg-sunk p-2">
          {offered.length > 12 && <Input aria-label={t("endpoints.filter")} placeholder={t("endpoints.filter")} value={filter} onChange={(e) => setFilter(e.target.value)} />}
          {offered.length === 0 && <p className="text-fg-3">{t("endpoints.loadEmpty")}</p>}
          <ul className="max-h-56 space-y-0.5 overflow-y-auto">
            {shown.map((id) => (
              <li key={id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-sm px-1.5 py-1 hover:bg-bg">
                  <input type="checkbox" checked={named.includes(id)} onChange={() => toggle(id)} />
                  <span className="min-w-0 truncate font-mono">{id}</span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
  return (
    <div className={clsx("space-y-3 rounded-sm p-3 ring-1 ring-line", !account.enabled && "opacity-60")}>
      {head}
      {kind}
      {ownedBy}
      <p className="text-fg-3">{t("accountStatus.progress", doneCount(status))}</p>
      <ol data-part="account-steps">
        <Step n={1} item={status[0]} title={t("accountStatus.step.connect")}>
          {connection}
        </Step>
        <Step n={2} item={status[1]} title={t("accountStatus.step.share")}>
          {sharing || <p className="text-fg-3">{t("accountStatus.share.installationHint")}</p>}
        </Step>
        <Step n={3} item={status[2]} title={t("accountStatus.step.projects")}>
          <p className="text-fg-3">{t(account.owner_id == null ? "accountStatus.projects.installationHint" : "accountStatus.projects.ownHint")}</p>
        </Step>
        <Step n={4} item={status[3]} title={status[3].label ?? t("accountStatus.step.limits")} last>
          <p className="text-fg-3">{t("accountStatus.use.hint")}</p>
        </Step>
      </ol>
    </div>
  );
}
