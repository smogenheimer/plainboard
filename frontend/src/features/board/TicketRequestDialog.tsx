import { useQueryClient } from "@tanstack/react-query";
import { Paperclip, Send } from "lucide-react";
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { useAutoGrow } from "../../lib/hooks";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import { qk } from "../../api/queries";
import type { Agent, Attachment } from "../../api/types";
import { PendingFile } from "../../components/PendingFile";
import { Button, Modal } from "../../components/ui";
import { useShell } from "../layout/shell-context";
import { useDraft, useDraftFiles } from "../../lib/drafts";

/** The manager knows a chat message by this start as a request for a ticket (prompts.py, "How work flows"). */
const PREFIX = "Ticket request:";

/** "Add ticket": the user describes the wish with files, and the manager turns it into a ticket. */
export function TicketRequestDialog({ manager, onClose, onSent }: { manager: Agent; onClose: () => void; onSent: () => void }) {
  const { t } = useTranslation();
  const { project } = useShell();
  const qc = useQueryClient();
  const [text, setText, clearText] = useDraft(`ticket-request:${project.id}`);
  const [files, setFiles] = useDraftFiles(`ticket-request:${project.id}`);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(area, text);
  useEffect(() => area.current?.focus(), []);

  const send = async () => {
    const value = text.trim();
    if (!value || sending) return;
    setError(null);
    setSending(true);
    try {
      let attachment_ids: number[] = [];
      if (files.length) {
        const form = new FormData();
        for (const file of files) form.append("files", file, file.name || "pasted.png");
        attachment_ids = (await api.post<Attachment[]>(`/agents/${manager.id}/attachments`, form)).map((a) => a.id);
      }
      await api.post(`/agents/${manager.id}/messages`, { text: `${PREFIX}\n\n${value}`, attachment_ids });
      qc.invalidateQueries({ queryKey: qk.runs(project.id) });
      clearText();
      setFiles([]);
      onSent();
    } catch (err) {
      setError(errorMessage(err));
      setSending(false);
    }
  };

  const hasFiles = (e: DragEvent) => e.dataTransfer.types.includes("Files");
  const onDrop = (e: DragEvent) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    setFiles((cur) => [...cur, ...Array.from(e.dataTransfer.files)]);
  };
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    // Office apps also put a picture of copied text on the clipboard: text wins.
    const pasted = Array.from(e.clipboardData.files);
    if (pasted.length && !e.clipboardData.getData("text/plain")) {
      e.preventDefault();
      setFiles((cur) => [...cur, ...pasted]);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={t("board.request.title")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" icon={<Send className="size-4" />} loading={sending} disabled={!text.trim()} onClick={send}>
            {t("board.request.send", { name: manager.name })}
          </Button>
        </>
      }
    >
      <div
        data-part="ticket-request"
        className="flex h-full flex-col gap-3"
        onDragOver={(e) => hasFiles(e) && e.preventDefault()}
        onDrop={onDrop}
      >
        <p className="text-fg-2">{t("board.request.intro", { name: manager.name })}</p>
        <textarea
          ref={area}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onPaste={onPaste}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder={t("board.request.placeholder")}
          aria-label={t("board.request.placeholder")}
          className="scroll-thin min-h-56 w-full flex-1 resize-none rounded-sm sm:max-h-[50vh] sm:flex-none bg-transparent px-3 py-2 ring-1 ring-line-2 outline-none ring-inset focus:ring-2 focus:ring-fg sm:min-h-64"
        />
        {files.length > 0 && (
          <div className="flex flex-wrap items-end gap-2">
            {files.map((file, i) => (
              <PendingFile key={`${file.name}-${file.lastModified}-${i}`} file={file} onRemove={() => setFiles(files.filter((_, j) => j !== i))} />
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              setFiles((cur) => [...cur, ...Array.from(e.target.files ?? [])]);
              e.target.value = "";
            }}
          />
          <Button variant="ghost" icon={<Paperclip className="size-4" />} onClick={() => picker.current?.click()}>
            {t("chat.attachFiles")}
          </Button>
          <span className="text-fg-3 max-sm:hidden">{t("board.request.filesHint")}</span>
        </div>
        {error && <p className="text-you-fg">{error}</p>}
      </div>
    </Modal>
  );
}
