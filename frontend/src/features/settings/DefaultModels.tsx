import { useTranslation } from "react-i18next";
import type { ProviderInfo } from "../../api/types";
import { Field, Select } from "../../components/ui";
import { ModelSelect } from "../team/AgentEditor";
import { ClassSelect, StrengthSelect } from "../team/ModelChoice";

type Values = Record<string, any>;

const ROLES = [
  // `strength` set: new agents get no model but that strength and `cls` (summaries are always allround).
  { model: "default_manager_model", strength: "default_manager_strength", cls: "default_manager_class", effort: "default_manager_effort", label: "settings.managerModel", hint: "setup.models.managerHint" },
  { model: "default_worker_model", strength: "default_worker_strength", cls: "default_worker_class", effort: "default_worker_effort", label: "settings.workerModel", hint: "setup.models.workerHint" },
  { model: "default_summary_model", strength: "default_summary_strength", cls: null, effort: "default_summary_effort", label: "settings.summaryModel", hint: "setup.models.summaryHint" },
] as const;

/** What new agents get, for managers, members and summaries: the model, or a strength (and class)
    PlainBoard chooses the model by, and beside it how long it thinks. */
export function DefaultModels({ provider, values, set }: { provider?: ProviderInfo; values: Values; set: (patch: Values) => void }) {
  const { t } = useTranslation();
  return (
    <>
      {ROLES.map((role) => {
        const effort: string = values[role.effort] ?? "";
        // A value the chosen provider does not list (it was set for another one) stays visible.
        const efforts = [...(provider?.efforts ?? []), ...(effort && !provider?.efforts.includes(effort) ? [effort] : [])];
        return (
          <Field key={role.model} group label={t(role.label)} hint={t(role.hint)}>
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="space-y-1.5">
                  <ModelSelect
                    provider={provider}
                    value={values[role.model] ?? ""}
                    onChange={(v) => set({ [role.model]: v })}
                    chosen={!!values[role.strength]}
                    onChosen={(on) => set({ [role.strength]: on ? (values[role.strength] ?? "medium") : null })}
                  />
                  {values[role.strength] && (
                    <div className="flex gap-2">
                      <div className="min-w-0 flex-1">
                        <StrengthSelect value={values[role.strength]} onChange={(v) => set({ [role.strength]: v })} />
                      </div>
                      {role.cls && (
                        <div className="min-w-0 flex-1">
                          <ClassSelect classes={values.model_classes} value={values[role.cls] ?? ""} onChange={(v) => set({ [role.cls]: v || null })} />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div className="w-32 shrink-0 sm:w-40">
                <Select aria-label={t("settings.defaultEffort")} value={effort} onChange={(e) => set({ [role.effort]: e.target.value || null })}>
                  <option value="">{t("settings.effortOwn")}</option>
                  {efforts.map((e) => (
                    <option key={e} value={e}>
                      {t("settings.effortOption", { effort: e })}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          </Field>
        );
      })}
      <p className="text-fg-3">{t("setup.models.effortHint")}</p>
    </>
  );
}
