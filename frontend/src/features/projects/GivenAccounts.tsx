import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { qk } from "../../api/queries";
import type { ProjectAccount } from "../../api/types";
import { ErrorText, Toggle } from "../../components/ui";

const key = (projectId: number) => ["given-accounts", projectId] as const;

/** Whoever works in the project gives it their own subscriptions here and takes them back; its owner
 *  sees what members gave it, with their names, and switches one off for the project. No secret, ever. */
export function GivenAccounts({
  projectId,
  isOwner,
}: {
  projectId: number;
  isOwner: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: key(projectId),
    queryFn: () =>
      api.get<ProjectAccount[]>(`/projects/${projectId}/given-accounts`),
  });
  const [error, setError] = useState<unknown>(null);
  const mine = (list.data ?? []).filter((a) => a.mine && (a.given || !a.used)); // one the project uses anyway needs no giving
  const others = (list.data ?? []).filter((a) => !a.mine && a.given);

  const put = async (path: string, body: Record<string, boolean>) => {
    setError(null);
    try {
      qc.setQueryData(
        key(projectId),
        await api.put<ProjectAccount[]>(`/projects/${projectId}/${path}`, body),
      );
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["project-accounts", projectId] }),
        qc.invalidateQueries({ queryKey: qk.accounts }),
      ]);
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="space-y-4" data-part="given-accounts">
      {(!isOwner || mine.length > 0) && (
        <div className="space-y-1.5">
          <span className="block font-medium text-fg-2">
            {t("givenAccounts.mine")}
          </span>
          {mine.length ? (
            <ul className="space-y-1">
              {mine.map((a) => (
                <li key={a.id} className="space-y-0.5">
                  <Toggle
                    checked={a.given}
                    onChange={(given) =>
                      put(`given-accounts/${a.id}`, { given })
                    }
                    label={<span className="min-w-0 truncate">{a.label}</span>}
                  />
                  {a.given && a.blocked && (
                    <p className="pl-11 text-fg-3">
                      {t("givenAccounts.blockedMine")}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-fg-3">{t("givenAccounts.noneMine")}</p>
          )}
          <p className="text-fg-3">{t("givenAccounts.mineHint")}</p>
        </div>
      )}
      {(isOwner || others.length > 0) && (
        <div className="space-y-1.5">
          <span className="block font-medium text-fg-2">
            {t("givenAccounts.others")}
          </span>
          {others.length ? (
            <ul className="space-y-1">
              {others.map((a) => (
                <li
                  key={a.id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-0.5"
                >
                  {isOwner ? (
                    <Toggle
                      checked={!a.blocked}
                      onChange={(on) =>
                        put(`blocked-accounts/${a.id}`, { blocked: !on })
                      }
                      label={
                        <span className="min-w-0 truncate">{a.label}</span>
                      }
                    />
                  ) : (
                    <span className="min-w-0 truncate">{a.label}</span>
                  )}
                  <span className="text-fg-3">
                    {t("givenAccounts.from", { name: a.owner_name })}
                    {a.blocked
                      ? ` · ${t("givenAccounts.blocked")}`
                      : !a.used
                        ? ` · ${t("givenAccounts.unused")}`
                        : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-fg-3">{t("givenAccounts.noneOthers")}</p>
          )}
          {isOwner && (
            <p className="text-fg-3">{t("givenAccounts.ownerHint")}</p>
          )}
        </div>
      )}
      <ErrorText error={error} />
    </div>
  );
}
