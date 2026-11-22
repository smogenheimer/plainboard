import { X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { ProviderInfo } from "../../api/types";
import { Button, ErrorText, IconButton, Input } from "../../components/ui";
import { classNames, FIXED_CLASSES, type ModelClasses as Classes } from "../../lib/models";

/** Which models belong to a class and how good each is in it: a score from 0 to 100 per model,
    kept by hand. Empty means the model is not of that class; a class nobody filled holds every model. */
export function ModelClasses({ providers, value, onChange }: { providers: ProviderInfo[]; value: Classes | null | undefined; onChange: (v: Classes) => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const classes = value ?? {};
  const names = classNames(classes);
  const listed = providers.filter((p) => p.models.length > 0);

  const setScore = (cls: string, provider: string, model: string, text: string) => {
    const scores = { ...(classes[cls]?.[provider] ?? {}) };
    const score = Number(text);
    if (text === "" || Number.isNaN(score)) delete scores[model];
    else scores[model] = Math.min(100, Math.max(0, score));
    onChange({ ...classes, [cls]: { ...(classes[cls] ?? {}), [provider]: scores } });
  };
  const add = () => {
    const cls = name.trim().toLowerCase();
    if (cls && !names.includes(cls)) onChange({ ...classes, [cls]: {} });
    setName("");
  };
  const remove = (cls: string) => onChange(Object.fromEntries(Object.entries(classes).filter(([k]) => k !== cls)));
  const fill = async () => {
    setBusy(true);
    setError(null);
    try {
      onChange((await api.post<{ classes: Classes }>("/settings/model-classes/arena", { classes })).classes);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <p className="font-medium">{t("modelChoice.classesTitle")}</p>
        <p className="text-fg-3">{t("modelChoice.classesHint")}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr>
              <th className="py-1 pr-3 font-medium">{t("modelChoice.model")}</th>
              {names.map((cls) => (
                <th key={cls} className="px-1 py-1 font-medium whitespace-nowrap">
                  <span className="inline-flex items-center gap-1">
                    {cls}
                    {!FIXED_CLASSES.includes(cls) && (
                      <IconButton label={t("modelChoice.removeClass", { name: cls })} onClick={() => remove(cls)}>
                        <X className="size-3.5" />
                      </IconButton>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {listed.map((provider) =>
              provider.models.map((model) => (
                <tr key={`${provider.name}/${model.id}`}>
                  <td className="py-1 pr-3 whitespace-nowrap">
                    {model.label} <span className="text-fg-3">{t(`modelChoice.size.${model.tier}`, { defaultValue: model.tier })}</span>
                  </td>
                  {names.map((cls) => (
                    <td key={cls} className="px-1 py-1">
                      <Input
                        type="number"
                        min={0}
                        max={100}
                        className="w-20"
                        aria-label={t("modelChoice.scoreOf", { model: model.label, name: cls })}
                        value={classes[cls]?.[provider.name]?.[model.id] ?? ""}
                        onChange={(e) => setScore(cls, provider.name, model.id, e.target.value)}
                      />
                    </td>
                  ))}
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="w-48">
          <Input value={name} placeholder={t("modelChoice.newClass")} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        </div>
        <Button variant="secondary" onClick={add} disabled={!name.trim()}>
          {t("modelChoice.addClass")}
        </Button>
        <Button variant="secondary" onClick={fill} disabled={busy}>
          {busy ? t("modelChoice.arenaBusy") : t("modelChoice.arena")}
        </Button>
      </div>
      <p className="text-fg-3">{t("modelChoice.arenaHint")}</p>
      <ErrorText error={error} />
    </div>
  );
}
