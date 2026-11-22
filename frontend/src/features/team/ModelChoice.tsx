import { useTranslation } from "react-i18next";
import { Select } from "../../components/ui";
import { classNames, SIZES, type ModelClasses } from "../../lib/models";

/** What the model is for. Empty is the usual class, allround. */
export function ClassSelect({ classes, value, onChange }: { classes?: ModelClasses | null; value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  // A class that was removed from the settings stays visible on the agent that still names it.
  const names = [...new Set([...classNames(classes), ...(value ? [value] : [])])].filter((n) => n !== "allround");
  return (
    <Select aria-label={t("modelChoice.class")} value={value === "allround" ? "" : value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{t("modelChoice.classOption", { name: "allround" })}</option>
      {names.map((name) => (
        <option key={name} value={name}>
          {t("modelChoice.classOption", { name })}
        </option>
      ))}
    </Select>
  );
}

/** How large the model is. `empty`: what no choice means, where there is one. */
export function StrengthSelect({ value, onChange, empty }: { value: string; onChange: (v: string) => void; empty?: string }) {
  const { t } = useTranslation();
  return (
    <Select aria-label={t("modelChoice.strength")} value={value} onChange={(e) => onChange(e.target.value)}>
      {empty !== undefined && <option value="">{empty}</option>}
      {SIZES.map((size) => (
        <option key={size} value={size}>
          {t(`modelChoice.size.${size}`)}
        </option>
      ))}
    </Select>
  );
}
