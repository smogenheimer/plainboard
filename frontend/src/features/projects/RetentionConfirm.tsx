import { useTranslation } from "react-i18next";
import type { RetentionPreview } from "../../api/types";
import { Button, ErrorText, Modal } from "../../components/ui";
import { formatBytes } from "../../lib/format";

/** Before a retention is saved: what it would delete, at once and soon (a project's, or the PlainBoard default's in all projects that follow it). */
export function RetentionConfirm({
  preview,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  preview: RetentionPreview | null;
  busy: boolean;
  error: unknown;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal
      open={preview !== null}
      onClose={() => !busy && onCancel()}
      width="max-w-lg"
      title={t("projects.retentionConfirmTitle")}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button variant="danger" loading={busy} onClick={onConfirm}>
            {t("projects.retentionConfirm")}
          </Button>
        </>
      }
    >
      {preview && (
        <div className="space-y-2">
          {preview.projects != null && (
            <p>
              {t("projects.retentionProjects", { count: preview.projects })}
            </p>
          )}
          <p>
            {t("projects.retentionNow", {
              count: preview.now.count,
              size: formatBytes(preview.now.bytes),
            })}
          </p>
          <p>
            {t("projects.retentionSoon", {
              days: preview.soon_days,
              count: preview.soon.count,
              size: formatBytes(preview.soon.bytes),
            })}
          </p>
          <p className="text-fg-3">{t("projects.retentionKept")}</p>
          <ErrorText error={error} />
        </div>
      )}
    </Modal>
  );
}

/** "never", or "after N days in done, files from M MB". */
export function retentionText(
  t: (key: string, options?: Record<string, unknown>) => string,
  days: number,
  minMb: number,
): string {
  if (days <= 0) return t("projects.retentionNever");
  return minMb > 0
    ? t("projects.retentionAfterFrom", { days, mb: minMb })
    : t("projects.retentionAfter", { days });
}
