import clsx from "clsx";
import { FileText, FileX, Trash2, Upload } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import type { Attachment } from "../api/types";
import { chatTime, formatBytes, formatDateTime } from "../lib/format";
import { isMediaFile, isVideoFile } from "../lib/media";
import { AttachmentLink } from "./AttachmentLink";
import { Badge, Button, IconButton } from "./ui";
import { VideoThumb } from "./VideoThumb";

/** Files of a ticket or an idea: thumbnails, upload by button or drop, removal. Media open via onView. */
export function AttachmentGrid({
  attachments,
  reviewIds,
  onUpload,
  onView,
  onRemove,
  canWrite,
}: {
  attachments: Attachment[];
  reviewIds?: Set<number>;
  onUpload: (files: FileList) => void;
  onView: (id: number) => void;
  onRemove: (attachment: Attachment) => void;
  canWrite: boolean;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    onUpload(e.dataTransfer.files);
  };

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="caps text-fg-2">{t("ticket.attachments")}</h3>
        {canWrite && (
          <Button size="sm" variant="ghost" icon={<Upload className="size-3.5" />} onClick={() => input.current?.click()}>
            {t("ticket.upload")}
          </Button>
        )}
      </div>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files) onUpload(e.target.files);
          e.target.value = "";
        }}
      />
      <div
        onDragOver={
          canWrite
            ? (e) => {
                e.preventDefault();
                setOver(true);
              }
            : undefined
        }
        onDragLeave={canWrite ? () => setOver(false) : undefined}
        onDrop={canWrite ? onDrop : undefined}
        className={clsx("rounded-sm p-2 transition-colors", canWrite && "border border-dashed", over ? "border-fg bg-sunk" : "border-line")}
      >
        {attachments.length === 0 ? (
          <p className="py-3 text-center text-fg-3">{t(canWrite ? "ticket.dropFiles" : "ticket.noFiles")}</p>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {attachments.map((a) => (
              <div key={a.id} className="group relative overflow-hidden rounded-sm ring-1 ring-line">
                {a.purged_at ? (
                  <div className="flex h-24 w-full flex-col items-center justify-center gap-1 bg-sunk px-2 text-center text-fg-3">
                    <FileX className="size-7 text-line-2" />
                    <span>{t("ticket.filePurged", { date: formatDateTime(a.purged_at), days: a.purged_after_days ?? 0 })}</span>
                  </div>
                ) : isMediaFile(a) ? (
                  <button type="button" onClick={() => onView(a.id)} className="block h-24 w-full cursor-zoom-in">
                    {isVideoFile(a) ? <VideoThumb src={`/api/attachments/${a.id}`} /> : <img src={`/api/attachments/${a.id}`} alt={a.filename} className="h-24 w-full object-cover" />}
                  </button>
                ) : (
                  <AttachmentLink file={a} className="flex h-24 w-full items-center justify-center bg-sunk">
                    <FileText className="size-8 text-line-2" />
                  </AttachmentLink>
                )}
                {a.purged_at ? (
                  <span className="block w-full truncate px-2 py-1 text-fg-3" title={a.filename}>
                    {a.filename} · {formatBytes(a.size)}
                  </span>
                ) : (
                  <AttachmentLink file={a} className="block w-full truncate px-2 py-1">
                    {a.filename} <span className="text-fg-3" title={formatDateTime(a.created_at)}>
                      · {formatBytes(a.size)} · {chatTime(a.created_at)}
                    </span>
                  </AttachmentLink>
                )}
                {reviewIds?.has(a.id) && (
                  <Badge tone="amber" className="absolute top-1 left-1">
                    {t("ticket.forReview")}
                  </Badge>
                )}
                {canWrite && (
                  <IconButton label={t("common.delete")} onClick={() => onRemove(a)} className="absolute top-1 right-1 bg-bg opacity-0 group-hover:opacity-100 touch:opacity-100">
                    <Trash2 className="size-3.5" />
                  </IconButton>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
