import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import { qk } from "../../api/queries";
import type { ProviderAccount } from "../../api/types";
import { Button, Input } from "../../components/ui";

/** The sign-in of a subscription in the browser: ChatGPT with a device code (Codex), or Claude and Google (Gemini),
 *  whose page shows a code after signing in that is typed in here. A person's own is kept for this subscription only. */
export function SignIn({ accountId, provider }: { accountId: number; provider: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const KEY = ["accountSignIn", accountId];
  const url = `/accounts/${accountId}/signin`;
  const texts = provider === "codex" ? "codex" : provider === "antigravity" ? "antigravity" : provider === "gemini_cli" ? "gemini" : "claude";
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const account = useQuery({
    queryKey: KEY,
    queryFn: () => api.get<ProviderAccount>(url),
    refetchInterval: (query) => (query.state.data?.login_code || query.state.data?.needs_code ? 3000 : false),
  });

  const act = async (action: () => Promise<ProviderAccount>, reload = false) => {
    setBusy(true);
    setError(null);
    try {
      qc.setQueryData(KEY, await action());
      if (reload) await qc.invalidateQueries({ queryKey: KEY });
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.accounts }),
        qc.invalidateQueries({ queryKey: qk.meta }),
        qc.invalidateQueries({ queryKey: qk.limits }),
      ]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const login = () => act(() => api.post<ProviderAccount>(url));
  const logout = () => act(() => api.del<ProviderAccount>(url), true);
  const send = () =>
    act(async () => {
      const state = await api.post<ProviderAccount>(`${url}/code`, { code: code.trim() });
      if (!state.login_error) setCode("");
      return state;
    });

  const data = account.data;
  const problem = error ?? data?.login_error ?? (account.error ? errorMessage(account.error) : null);
  const openPageText = provider === "antigravity" ? "settings.antigravityPasteCode" : `settings.${texts}OpenPage`;
  const codePlaceholder = provider === "antigravity" ? "settings.antigravityCodePlaceholder" : "settings.claudeCode";
  const sendCodeText = provider === "antigravity" ? "settings.antigravitySubmitCode" : "settings.claudeSendCode";
  return (
    <div className="space-y-2 rounded-sm bg-sunk p-3 text-fg-2" data-part="sign-in">
      {data?.login_code || data?.needs_code ? (
        <>
          <p>{t(data.needs_code ? openPageText : "settings.codexDeviceCode")}</p>
          <a href={data.login_url ?? "#"} target="_blank" rel="noreferrer" className="block font-medium break-all text-fg underline">
            {data.login_url}
          </a>
          {data.login_code && <p className="font-mono font-semibold tracking-widest text-fg select-all">{data.login_code}</p>}
          {data.needs_code && (
            <div className="flex flex-wrap gap-2">
              <Input
                aria-label={t(codePlaceholder)}
                className="min-w-0 flex-1 basis-48 font-mono"
                autoComplete="off"
                placeholder={t(codePlaceholder)}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && code.trim() && send()}
              />
              <Button size="md" variant="primary" onClick={send} disabled={!code.trim()} loading={busy}>
                {t(sendCodeText)}
              </Button>
            </div>
          )}
          <div className="flex items-center gap-3">
            {data.login_code && <span className="animate-pulse">{t("settings.codexWaiting")}</span>}
            <Button size="sm" variant="ghost" onClick={logout} loading={busy && !data.needs_code}>
              {t("settings.codexCancel")}
            </Button>
          </div>
        </>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="min-w-0 flex-1">
            {data?.signed_in
              ? data.detail
                ? t("settings.codexSignedIn", { detail: data.detail })
                : t("settings.signedIn")
              : account.isLoading
                ? t("settings.testing")
                : t("settings.codexSignedOut")}
          </span>
          <Button size="sm" onClick={data?.signed_in ? logout : login} loading={busy}>
            {data?.signed_in ? t("settings.codexSignOut") : t(`settings.${texts}SignIn`)}
          </Button>
        </div>
      )}
      {problem && <p className="text-you-fg">{problem}</p>}
    </div>
  );
}
