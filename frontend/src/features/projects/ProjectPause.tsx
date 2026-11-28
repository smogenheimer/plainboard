import { useMutation, useQueryClient } from "@tanstack/react-query";
import { PauseCircle } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { qk } from "../../api/queries";
import type { Project } from "../../api/types";
import { ErrorText, Toggle } from "../../components/ui";
import { whenAhead } from "../../lib/format";

/** Pause or resume one project. Paused, nothing of it starts until a person resumes it here.
 *  The switch flips at once; if the request fails, it flips back and the error is shown. */
function usePauseSwitch(project: Project) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (on: boolean) => (on ? api.post<Project>(`/projects/${project.id}/pause`) : api.del<Project>(`/projects/${project.id}/pause`)),
    onMutate: async (on: boolean) => {
      await qc.cancelQueries({ queryKey: qk.projects });
      const before = qc.getQueryData<Project[]>(qk.projects);
      qc.setQueryData<Project[]>(qk.projects, (list) =>
        list?.map((p) => (p.id === project.id ? { ...p, paused_at: on ? p.paused_at || new Date().toISOString() : null, paused_by: on ? p.paused_by : "" } : p)),
      );
      return { before };
    },
    onError: (_error, _on, context) => {
      if (context?.before) qc.setQueryData(qk.projects, context.before);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.projects }),
  });
}

const since = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

/** PlainBoard's own pause before the subscriptions run out, with when it goes on. Empty: not paused so. */
function useLimitPause(project: Project): string {
  const { t } = useTranslation();
  if (!project.limit_paused_at) return "";
  return project.limit_resumes_at ? t("projects.limitPaused", { when: whenAhead(project.limit_resumes_at) }) : t("projects.limitPausedUnknown");
}

/** Read the subscriptions' limits now: a plan topped up ends PlainBoard's pause at once, not with its next reading. */
function useLimitCheck(project: Project) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Project>(`/projects/${project.id}/limit-check`),
    onSuccess: (fresh) => qc.setQueryData<Project[]>(qk.projects, (list) => list?.map((p) => (p.id === fresh.id ? { ...p, ...fresh } : p))),
  });
}

/** The switch in the project's settings, with what it means. */
export function ProjectPauseSwitch({ project }: { project: Project }) {
  const { t } = useTranslation();
  const turn = usePauseSwitch(project);
  const limited = useLimitPause(project);
  return (
    <div className="space-y-1">
      <Toggle checked={!!project.paused_at} onChange={(on) => turn.mutate(on)} label={t("projects.pause")} />
      <p className="text-fg-3">
        {project.paused_at && `${t("projects.pausedSince", { by: project.paused_by || t("pause.someone"), when: since(project.paused_at) })} `}
        {t("projects.pauseHint")}
      </p>
      {limited && <p className="text-fg-2">{limited}</p>}
      <ErrorText error={turn.error} />
    </div>
  );
}

/** Said on every view of a paused project, so a message that gets no answer is no riddle. An owner goes on from here. */
export function ProjectPauseBanner({ project, isOwner }: { project: Project; isOwner: boolean }) {
  const { t } = useTranslation();
  const turn = usePauseSwitch(project);
  const limited = useLimitPause(project);
  const check = useLimitCheck(project);
  const { mutate: checkNow } = check;
  const mayCheck = project.role !== "viewer";
  const limitPaused = !!project.limit_paused_at;
  // Opened while paused for its limits: read them at once, the plan may have been topped up since.
  useEffect(() => {
    if (limitPaused && mayCheck) checkNow();
  }, [project.id, limitPaused, mayCheck, checkNow]);
  if (!project.paused_at && limited) {
    return (
      <div role="status" className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-you-bg px-4 py-1.5 text-you-fg">
        <PauseCircle className="size-4 shrink-0" />
        <span className="min-w-0 flex-1 basis-48">
          {limited}
          {check.isSuccess && !check.isPending && ` ${t("projects.limitStill")}`}
        </span>
        {mayCheck && (
          <button type="button" disabled={check.isPending} onClick={() => checkNow()} className="shrink-0 font-semibold underline underline-offset-[3px] hover:decoration-2 disabled:opacity-60">
            {check.isPending ? t("projects.limitChecking") : t("projects.limitCheck")}
          </button>
        )}
        <ErrorText error={check.error} />
      </div>
    );
  }
  if (!project.paused_at) return null;
  return (
    <div role="status" className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-you-bg px-4 py-1.5 text-you-fg">
      <PauseCircle className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 basis-48">{t("projects.pausedBanner", { by: project.paused_by || t("pause.someone") })}</span>
      {isOwner && (
        <button type="button" disabled={turn.isPending} onClick={() => turn.mutate(false)} className="shrink-0 font-semibold underline underline-offset-[3px] hover:decoration-2 disabled:opacity-60">
          {t("projects.resume")}
        </button>
      )}
      <ErrorText error={turn.error} />
    </div>
  );
}
