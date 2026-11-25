import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { useAccounts, useMe } from "../../api/queries";
import type { Account, Project } from "../../api/types";
import { StatusMark, useAccountStatus } from "../settings/AccountStatus";

/** The accounts a project may choose from: all of them, or the ones an admin limited it to, and of
 *  those only what its owner may use; with what members gave it (``usable``, from the server, which knows all of that). */
export const allowedAccounts = (project: Pick<Project, "account_ids"> | null | undefined, accounts: Account[], usable?: number[]) =>
  accounts.filter(
    (a) => (!usable || usable.includes(a.id)) && (!project?.account_ids?.length || project.account_ids.includes(a.id) || (usable != null && a.owner_id != null)),
  );

/** The ids of the subscriptions a project's agents may run on: its own limit and what its owner may use. */
export const useUsableAccounts = (projectId: number | null | undefined) =>
  useQuery({ queryKey: ["project-accounts", projectId], queryFn: () => api.get<number[]>(`/projects/${projectId}/accounts`), enabled: projectId != null }).data;

/** The same choice, whatever order it was ticked in. */
export const sameAccounts = (a: number[], b: number[]) => a.length === b.length && a.every((id) => b.includes(id));

/** Which of the installation's subscriptions this project may use. An admin ticks them, and they are
    stored with the rest of the form; everyone else reads what applies. Nothing ticked: any. */
export function ProjectAccounts({ value, onChange }: { value: number[]; onChange: (ids: number[]) => void }) {
  const { t } = useTranslation();
  const me = useMe();
  const accounts = useAccounts().data ?? [];

  if (!me?.is_admin) {
    if (!value.length) return null;
    const names = accounts.filter((a) => value.includes(a.id)).map((a) => a.label);
    return <p className="text-fg-2">{t("projectAccounts.readonly", { names: names.join(", ") })}</p>;
  }
  if (accounts.length < 2 && !value.length) return null; // one subscription: nothing to choose between

  return (
    <div className="space-y-1">
      <span className="block font-medium text-fg-2">{t("projectAccounts.label")}</span>
      <ul className="space-y-0.5">
        {accounts.map((a) => (
          <li key={a.id}>
            <label className="flex cursor-pointer flex-wrap items-center gap-x-2 gap-y-0.5 rounded-sm px-1.5 py-1 hover:bg-sunk">
              <input
                type="checkbox"
                checked={value.includes(a.id)}
                onChange={() => onChange(value.includes(a.id) ? value.filter((id) => id !== a.id) : [...value, a.id])}
              />
              <span className="min-w-0 truncate">{a.label}</span>
              <AccountHints account={a} />
            </label>
          </li>
        ))}
      </ul>
      <p className="text-fg-3">{t("projectAccounts.hint")}</p>
    </div>
  );
}

/** #140: what still keeps a subscription from showing up here: no sign-in yet, limits not read yet. */
function AccountHints({ account }: { account: Account }) {
  const open = useAccountStatus(account).filter((item) => (item.key === "login" || item.key === "limits") && (item.tone === "todo" || item.tone === "pending"));
  return open.map((item) => (
    <span key={item.key} className={item.tone === "todo" ? "flex flex-none items-center gap-1.5 text-you-fg" : "flex flex-none items-center gap-1.5 text-fg-3"}>
      <StatusMark tone={item.tone} />
      {item.text}
    </span>
  ));
}
