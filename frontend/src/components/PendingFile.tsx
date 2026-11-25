import clsx from "clsx";
import { Paperclip, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Lightbox } from "./Lightbox";

/** A file picked or pasted but not sent yet: a thumbnail for an image (a click shows it large), else its name; removable. */
export function PendingFile({ file, onRemove }: { file: File; onRemove: () => void }) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState<string>();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!file.type.startsWith("image/")) return;
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const name = file.name || "pasted.png";
  return (
    <div className="relative max-w-40" title={name}>
      {preview ? (
        <button type="button" onClick={() => setOpen(true)} aria-label={t("chat.viewFile", { name })} className="block cursor-zoom-in rounded-sm">
          <img src={preview} alt={name} className="size-16 rounded-sm object-cover ring-1 ring-line-2" />
        </button>
      ) : (
        <div className="flex h-8 items-center gap-1.5 rounded-sm pr-8 pl-2 text-fg ring-1 ring-line-2 ring-inset">
          <Paperclip className="size-4 shrink-0 text-fg-3" />
          <span className="truncate">{name}</span>
        </div>
      )}
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("chat.removeFile", { name })}
        className={clsx("absolute flex size-6 items-center justify-center rounded-sm text-fg-2 hover:text-fg", preview ? "top-0.5 right-0.5 bg-bg" : "top-1 right-1")}
      >
        <X className="size-4" />
      </button>
      {open && preview && <Lightbox images={[{ src: preview, alt: name }]} onClose={() => setOpen(false)} />}
    </div>
  );
}
