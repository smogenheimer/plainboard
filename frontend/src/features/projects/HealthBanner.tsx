import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { HealthIssue, Project } from "../../api/types";

/** Shown here because nothing else on the screen says it: pauses have banners of their own (#22). */
const SHOWN = ["runner_away", "runner_updating", "manager_off", "parallel_zero"];

/** What keeps this project from working that no other banner names: its runner away, its manager off, no member allowed to work. */
export function HealthBanner({ project }: { project: Project }) {
  const { t } = useTranslation();
  const { data } = useQuery({
    queryKey: ["status", project.id],
    queryFn: () => api.get<{ issues: HealthIssue[] }>(`/status?project_id=${project.id}`),
    refetchInterval: 30_000,
  });
  const issues = (data?.issues ?? []).filter((i) => i.project_id === project.id && SHOWN.includes(i.code));
  if (!issues.length) return null;
  return (
    <div role="status" data-part="health" className="flex shrink-0 items-start gap-3 border-b border-line bg-you-bg px-4 py-1.5 text-you-fg">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <ul className="min-w-0 flex-1">
        {issues.map((i) => (
          <li key={i.code}>{t(`health.${i.code}`, { agent: i.agent, count: i.count })}</li>
        ))}
      </ul>
    </div>
  );
}
