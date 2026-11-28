import { useQueryClient } from "@tanstack/react-query";
import { Send, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import { upsertIdea } from "../../api/events";
import type { Agent, Idea } from "../../api/types";
import { Button, IconButton, Textarea } from "../../components/ui";
import { useDraft } from "../../lib/drafts";
import { sendsOnEnter } from "../../lib/hooks";
import { useShell } from "../layout/shell-context";
import { ideaTitle } from "./IdeaCard";

/** Sends ideas to the manager: the server writes the chat message ("Ideas handed over:", services/ideas.hand_over)
 * and marks the ideas, so nobody hands them over twice. */
function useHandOver(onSent: (count: number) => void) {
  const qc = useQueryClient();
  const pid = useShell().project.id;
  const [text, setText, clearText] = useDraft(`idea-handover:${pid}`);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const send = async (ids: number[]) => {
    if (sending || !ids.length) return;
    setSending(true);
    setError("");
    try {
      const out = await api.post<{ agent_id: number; ideas: Idea[] }>(`/projects/${pid}/ideas/hand-over`, { idea_ids: ids, message: text.trim() });
      out.ideas.forEach((idea) => upsertIdea(qc, pid, idea));
      clearText();
      setSending(false);
      onSent(ids.length);
    } catch (err) {
      setError(errorMessage(err));
      setSending(false);
    }
  };
  return { text, setText, sending, error, send };
}

// Like the idea input it takes the place of: Enter sends (a new line on touch screens), Shift+Enter is a new line.
const enter = (go: () => void) => (e: React.KeyboardEvent) => {
  if (sendsOnEnter(e)) {
    e.preventDefault();
    go();
  }
};

/** Ideas to the manager (#163): collected from their cards into a bar in place of the input, sent with an optional note. */
export function HandOverBar({
  manager,
  ideas,
  onRemove,
  onClose,
  onSent,
}: {
  manager: Agent;
  ideas: Idea[];
  onRemove: (id: number) => void;
  onClose: () => void;
  onSent: (count: number) => void;
}) {
  const { t } = useTranslation();
  const { text, setText, sending, error, send } = useHandOver(onSent);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => area.current?.focus(), []);
  const go = () => void send(ideas.map((i) => i.id));
  return (
    <div data-part="idea-handover-bar" className="pointer-events-auto flex w-full max-w-2xl flex-col gap-2 rounded-2xl bg-[var(--card,var(--bg))] p-3 shadow-lg ring-1 ring-line-2">
      <div className="flex items-center gap-2">
        <Send className="size-4 shrink-0 text-fg-2" />
        <span className="min-w-0 flex-1 font-semibold">{t("ideas.handOver.barTitle", { name: manager.name, count: ideas.length })}</span>
        <IconButton label={t("common.cancel")} onClick={onClose}>
          <X className="size-4" />
        </IconButton>
      </div>
      <div className="scroll-thin flex max-h-24 flex-wrap gap-1.5 overflow-y-auto">
        {ideas.map((idea) => (
          <span key={idea.id} className="inline-flex max-w-full items-center gap-1 rounded-full bg-sunk py-0.5 pr-0.5 pl-2.5 text-[0.92em]">
            <span className="min-w-0 truncate">{ideaTitle(idea)}</span>
            <button type="button" aria-label={t("ideas.handOver.remove")} onClick={() => onRemove(idea.id)} className="rounded-full p-0.5 text-fg-3 hover:text-fg touch:p-2">
              <X className="size-3.5" />
            </button>
          </span>
        ))}
        {!ideas.length && <span className="text-fg-3">{t("ideas.handOver.barEmpty")}</span>}
      </div>
      <div className="flex items-end gap-2">
        <Textarea
          ref={area}
          rows={1}
          value={text}
          maxLength={10000}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={enter(go)}
          placeholder={t("ideas.handOver.placeholder", { name: manager.name })}
          aria-label={t("ideas.handOver.placeholder", { name: manager.name })}
          className="max-h-40 min-w-0 flex-1"
        />
        <Button variant="primary" icon={<Send className="size-4" />} loading={sending} disabled={!ideas.length} onClick={go} aria-label={t("ideas.handOver.send", { name: manager.name })}>
          <span className="max-sm:hidden">{t("ideas.handOver.send", { name: manager.name })}</span>
        </Button>
      </div>
      {error && <p className="text-you-fg">{error}</p>}
    </div>
  );
}
