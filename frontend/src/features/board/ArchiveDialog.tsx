import { Search } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useArchivedTasks } from "../../api/queries";
import { ErrorText, Input, Modal, Spinner } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useShell } from "../layout/shell-context";
import { useTicketFilter } from "./ticketFilter";

const SHOWN = 200;

/** Tickets taken off the board. Opening one shows it as usual, where it can be restored. */
export function ArchiveDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { project, openTask } = useShell();
  const tasks = useArchivedTasks(project.id);
  const [filter, setFilter] = useState("");

  const { matches } = useTicketFilter(project.id, filter, tasks.data);

  return (
    <Modal open onClose={onClose} title={t("archive.title")}>
      <p className="mb-3 text-fg-3">{project.auto_archive_days ? t("archive.hint", { days: project.auto_archive_days }) : t("archive.hintManual")}</p>
      <div className="relative mb-3">
        <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-fg-3" />
        <Input autoFocus className="pl-8" placeholder={t("board.filter")} value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      {tasks.isLoading && (
        <div className="flex justify-center py-6">
          <Spinner />
        </div>
      )}
      <ErrorText error={tasks.error} />
      {tasks.data && !matches.length && <p className="py-6 text-center text-fg-3">{t("archive.empty")}</p>}
      <ul className="divide-y divide-line">
        {matches.slice(0, SHOWN).map((task) => (
          <li key={task.id}>
            <button
              type="button"
              onClick={() => {
                onClose();
                openTask(task.id);
              }}
              className="flex w-full items-center gap-2 px-1 py-2 text-left hover:bg-sunk"
            >
              <span className="text-fg-3">#{task.number}</span>
              <span className="min-w-0 flex-1 truncate">{task.title}</span>
              <span className="shrink-0 text-fg-3 max-sm:hidden">{t(`taskStatus.${task.status}`)}</span>
              <span className="w-24 shrink-0 text-right text-fg-3">{timeAgo(task.archived_at)}</span>
            </button>
          </li>
        ))}
      </ul>
      {matches.length > SHOWN && <p className="pt-2 text-center text-fg-3">{t("archive.more", { count: matches.length - SHOWN })}</p>}
    </Modal>
  );
}
