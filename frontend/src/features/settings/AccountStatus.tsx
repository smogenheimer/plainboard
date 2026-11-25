import { useQueries, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { qk, useAccounts, useDirectory, useLimits, useMe, useMeta, useProjects } from "../../api/queries";
import type { Account, Agent, ProviderAccount } from "../../api/types";
import { timeAgo } from "../../lib/format";

export type StatusKey = "login" | "share" | "projects" | "limits";
/** done: in order; todo: waits for you; pending: comes by itself; off: does not apply. */
export type StatusTone = "done" | "todo" | "pending" | "off";
export interface StatusItem {
  key: StatusKey;
  /** What the step is called; an API access shows when it is used instead of its limits. */
  label?: string;
  tone: StatusTone;
  text: string;
}

const LOGIN_MODES = ["runner_login", "chatgpt", "google"];
const fromUnix = (seconds: number) => new Date(seconds * 1000).toISOString();

/** Where a subscription stands, in four steps: signed in, shared with whom, which projects run on it, whether its limits are known. */
export function useAccountStatus(account: Account): StatusItem[] {
  const { t } = useTranslation();
  const me = useMe();
  const people = useDirectory().data ?? [];
  const projects = useProjects().data ?? [];
  const usable = useQueries({
    queries: projects.map((p) => ({ queryKey: ["project-accounts", p.id], queryFn: () => api.get<number[]>(`/projects/${p.id}/accounts`) })),
  });
  const login = LOGIN_MODES.includes(account.auth_mode);
  const signin = useQuery({
    queryKey: ["accountSignIn", account.id],
    queryFn: () => api.get<ProviderAccount>(`/accounts/${account.id}/signin`),
    enabled: login && account.can_manage,
    retry: false,
  });
  const limits = useLimits().data?.accounts.find((a) => a.account_id === account.id);

  const endpoint = useMeta().data?.endpoints.find((p) => p.name === account.provider)?.endpoint;
  const signedIn = endpoint && !endpoint.key ? true : login ? (signin.data?.signed_in ?? (account.secret_set ? true : signin.isError ? null : undefined)) : account.secret_set || account.secret_from_env;
  const loginItem: StatusItem = !account.enabled
    ? { key: "login", tone: "off", text: t("accountStatus.login.off") }
    : signedIn
      ? { key: "login", tone: "done", text: t(login ? "accountStatus.login.signedIn" : endpoint && !endpoint.key && !account.secret_set ? "accountStatus.login.noKeyNeeded" : "accountStatus.login.keySet") }
      : signedIn === false
        ? { key: "login", tone: "todo", text: t(login ? "accountStatus.login.signedOut" : "accountStatus.login.keyMissing") }
        : { key: "login", tone: "pending", text: t("accountStatus.login.unknown") };

  const owner = account.owner_id === me?.id ? t("accountStatus.share.you") : account.owner_name;
  const names = people.filter((p) => account.shared_with.includes(p.id)).map((p) => p.name || p.username);
  const shareItem: StatusItem =
    account.owner_id == null
      ? { key: "share", tone: "done", text: t("accountStatus.share.installation") }
      : account.share === "everyone"
        ? { key: "share", tone: "done", text: t("accountStatus.share.everyone") }
        : account.share === "people" && names.length
          ? { key: "share", tone: "done", text: names.join(", ") }
          : account.share === "people"
            ? { key: "share", tone: "todo", text: t("accountStatus.share.nobodyChosen") }
            : { key: "share", tone: "todo", text: t("accountStatus.share.private", { name: owner }) };

  const running = projects.filter((_, i) => usable[i]?.data?.includes(account.id)).map((p) => p.name);
  const projectsItem: StatusItem = !account.enabled
    ? { key: "projects", tone: "off", text: t("accountStatus.projects.none") }
    : running.length
      ? { key: "projects", tone: "done", text: running.length > 3 ? t("accountStatus.projects.more", { names: running.slice(0, 2).join(", "), count: running.length - 2 }) : running.join(", ") }
      : { key: "projects", tone: "todo", text: t(signedIn === false ? "accountStatus.projects.notYet" : "accountStatus.projects.none") };

  const use = useApiUse(account);
  const limitsItem: StatusItem = use
    ? { key: "limits", label: t("accountStatus.label.use"), ...use }
    : limits?.windows.length && limits.updated_at
        ? { key: "limits", tone: "done", text: t("accountStatus.limits.known", { when: timeAgo(fromUnix(limits.updated_at)) }) }
        : { key: "limits", tone: "pending", text: t("accountStatus.limits.pending") };

  return [loginItem, shareItem, projectsItem, limitsItem];
}

/** When an API access (no plan limits, so never a ring under the chat input) is used; null for a subscription.
 *  An endpoint runs the agents set to its provider. An API key of Claude or Codex waits until every subscription
 *  of its provider is full (accounts.pick: subscriptions first, API keys after), unless a project or agent is set to it. */
export function useApiUse(account: Account | undefined): { tone: StatusTone; text: string } | null {
  const { t } = useTranslation();
  const meta = useMeta().data;
  const accounts = useAccounts().data ?? [];
  const projects = useProjects().data ?? [];
  const api_ = account?.auth_mode === "api_key";
  const agents = useQueries({
    queries: projects.map((p) => ({ queryKey: qk.agents(p.id), queryFn: () => api.get<Agent[]>(`/projects/${p.id}/agents`), enabled: api_ })),
  })
    .flatMap((q) => q.data ?? [])
    .filter((a) => a.enabled);
  if (!account || !api_) return null;
  const endpoint = meta?.endpoints.find((p) => p.name === account.provider);
  const provider = (endpoint ?? meta?.providers.find((p) => p.name === account.provider))?.label ?? account.provider;
  if (endpoint) {
    const count = agents.filter((a) => a.provider === account.provider).length;
    return count ? { tone: "done", text: t("accountStatus.use.agents", { count, provider }) } : { tone: "todo", text: t("accountStatus.use.noAgents", { provider }) };
  }
  const pinned = [
    ...projects.filter((p) => p.account_id === account.id).map((p) => p.name),
    ...agents.filter((a) => a.account_id === account.id).map((a) => a.name),
  ];
  if (pinned.length) return { tone: "done", text: t("accountStatus.use.pinned", { names: pinned.slice(0, 3).join(", ") }) };
  const plans = accounts.filter((a) => a.provider === account.provider && a.enabled && a.auth_mode !== "api_key");
  return plans.length ? { tone: "pending", text: t("accountStatus.use.standby", { provider }) } : { tone: "done", text: t("accountStatus.use.only", { provider }) };
}

/** The mark of DESIGN.md's fixed cell for a step: check = in order, vermilion diamond = waits for you, hollow = comes by itself. */
export function StatusMark({ tone, className }: { tone: StatusTone; className?: string }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "mk",
        tone === "done" && "mk-done",
        tone === "todo" && "mk-you",
        tone === "pending" && "mk-plan",
        tone === "off" && "h-px w-2 self-center bg-fg-3",
        className,
      )}
    />
  );
}

/** How many of the steps that apply leave nothing to do: in order, or coming by themselves (limits not read yet, an API key on standby). */
export const doneCount = (items: StatusItem[]) => ({
  done: items.filter((i) => i.tone === "done" || i.tone === "pending").length,
  of: items.filter((i) => i.tone !== "off").length,
});
