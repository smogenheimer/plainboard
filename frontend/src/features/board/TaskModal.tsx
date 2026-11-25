import { useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Bot, Check, ChevronDown, ChevronRight, CircleDashed, Play, Send, Trash2, UserRound, X } from "lucide-react";
import { useEffect, useRef, useState, type ClipboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../api/client";
import { qk, useTask } from "../../api/queries";
import type { Assignment, Attachment, Comment, Priority, TaskDetail, TaskStatus } from "../../api/types";
import { AttachmentGrid } from "../../components/AttachmentGrid";
import { Lightbox } from "../../components/Lightbox";
import { isVideoFile } from "../../lib/media";
import { Markdown, TicketText } from "../../components/Markdown";
import { useMentionPicker, useReadMentionsOf } from "../../components/Mentions";
import { AgentSquare, AgentDot, Badge, Button, ErrorText, IconButton, Input, Modal, Segmented, Select, Spinner, Textarea } from "../../components/ui";
import { formatDateTime, timeAgo } from "../../lib/format";
import { useDraft } from "../../lib/drafts";
import { useShell } from "../layout/shell-context";
import { STATUSES } from "./Board";
import { Decisions, isMedia, reviewAttachmentIds } from "./Decisions";
import { FlowTicket } from "../flow/Integrations";
import { Label } from "./TaskCard";
import { languageName } from "../projects/CustomerLanguages";

const PRIORITIES: Priority[] = ["low", "medium", "high", "urgent"];

export function TaskModal({ taskId, onClose }: { taskId: number; onClose: () => void }) {
  const { t } = useTranslation();
  const { project, canWrite } = useShell();
  const qc = useQueryClient();
  const task = useTask(taskId);
  const [error, setError] = useState<unknown>(null);
  const [viewing, setViewing] = useState<number | null>(null);

  const patch = async (body: Omit<Partial<TaskDetail>, "customer_texts"> & { archived?: boolean; customer_texts?: CustomerBody["customer_texts"] }) => {
    setError(null);
    try {
      await api.patch(`/tasks/${taskId}`, body);
      qc.invalidateQueries({ queryKey: qk.task(taskId) });
      qc.invalidateQueries({ queryKey: qk.tasks(project.id) });
    } catch (err) {
      setError(err);
    }
  };

  const upload = async (files: FileList | File[]) => {
    if (!files.length) return;
    const form = new FormData();
    for (const file of Array.from(files)) form.append("files", file, file.name || "pasted.png");
    try {
      await api.post(`/tasks/${taskId}/attachments`, form);
      qc.invalidateQueries({ queryKey: qk.task(taskId) });
    } catch (err) {
      setError(err);
    }
  };

  const removeAttachment = async (a: Attachment) => {
    try {
      await api.del(`/attachments/${a.id}`);
      qc.invalidateQueries({ queryKey: qk.task(taskId) });
    } catch (err) {
      setError(err);
    }
  };

  const onPaste = (e: ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files);
    if (files.length) {
      e.preventDefault();
      void upload(files);
    }
  };

  if (!task.data) {
    return (
      <Modal open onClose={onClose} title="">
        <div className="flex justify-center py-10">{task.error ? <ErrorText error={task.error} /> : <Spinner />}</div>
      </Modal>
    );
  }
  const data = task.data;
  const images = data.attachments.filter((a) => isMedia(a) && !a.purged_at);

  const handToManager = async () => {
    setError(null);
    try {
      await api.post(`/tasks/${taskId}/start`);
      qc.invalidateQueries({ queryKey: qk.task(taskId) });
    } catch (err) {
      setError(err);
    }
  };

  const remove = async () => {
    if (!confirm(t("ticket.confirmDelete", { number: data.number }))) return;
    await api.del(`/tasks/${taskId}`);
    qc.invalidateQueries({ queryKey: qk.tasks(project.id) });
    onClose();
  };

  return (
    <Modal
      open
      onClose={onClose}
      width="max-w-4xl"
      title={
        <div className="flex items-center gap-2">
          <span className="font-normal text-fg-3">#{data.number}</span>
          <TitleEditor value={data.title} onSave={(title) => patch({ title })} canWrite={canWrite} />
        </div>
      }
    >
      {data.archived_at && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-sm bg-sunk px-3 py-2 text-fg-2">
          <Archive className="size-4 shrink-0" />
          <span className="flex-1">{t("archive.archivedOn", { date: formatDateTime(data.archived_at) })}</span>
          {canWrite && (
            <Button size="sm" icon={<ArchiveRestore className="size-3.5" />} onClick={() => patch({ archived: false })}>
              {t("archive.restore")}
            </Button>
          )}
        </div>
      )}
      {data.user_action && canWrite && <UserAction taskId={taskId} action={data.user_action} />}
      <div className="mb-6 empty:hidden">
        <Decisions decisions={data.decisions} attachments={data.attachments} onView={setViewing} />
      </div>
      <div className="grid gap-6 md:grid-cols-[1fr_220px]" onPaste={canWrite ? onPaste : undefined}>
        <div className="min-w-0 space-y-6">
          <FlowTicket taskId={taskId} />
          <DescriptionEditor draftKey={`description:${taskId}`} value={data.description} onSave={(description) => patch({ description })} canWrite={canWrite} />
          <CustomerTexts task={data} languages={project.customer_languages?.length ? project.customer_languages : ["en"]} onSave={patch} canWrite={canWrite} />
          <AttachmentGrid
            attachments={data.attachments}
            reviewIds={reviewAttachmentIds(data.decisions)}
            onUpload={upload}
            onView={setViewing}
            onRemove={removeAttachment}
            canWrite={canWrite}
          />
          {data.state && (
            <section>
              <SectionTitle right={data.state_updated_at && <span className="text-fg-3">{timeAgo(data.state_updated_at)}</span>}>{t("ticket.state")}</SectionTitle>
              <div className="rounded-sm bg-sunk px-3 py-2" title={t("ticket.stateHint")}>
                <Markdown>{data.state}</Markdown>
              </div>
            </section>
          )}
          <Assignments assignments={data.assignments} canWrite={canWrite} />
          <Comments key={taskId} taskId={taskId} comments={data.comments} total={data.comment_count} canWrite={canWrite} />
        </div>
        <aside className="order-first space-y-4 md:order-none">
          {canWrite && (
            <>
              <Button variant="primary" className="w-full" icon={<Play className="size-3.5" />} onClick={handToManager} disabled={data.manager_active}>
                {data.manager_active ? t("ticket.withManager") : t("ticket.handToManager")}
              </Button>
              <p className="-mt-2 text-fg-3">{t("ticket.handToManagerHint")}</p>
            </>
          )}
          <SideField label={t("ticket.status")}>
            {canWrite ? (
              <Select value={data.status} onChange={(e) => patch({ status: e.target.value as TaskStatus })}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t(`taskStatus.${s}`)}
                  </option>
                ))}
              </Select>
            ) : (
              <div>{t(`taskStatus.${data.status}`)}</div>
            )}
          </SideField>
          <SideField label={t("ticket.priority")}>
            {canWrite ? (
              <Select value={data.priority} onChange={(e) => patch({ priority: e.target.value as Priority })}>
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {t(`priority.${p}`)}
                  </option>
                ))}
              </Select>
            ) : (
              <div>{t(`priority.${data.priority}`)}</div>
            )}
          </SideField>
          <SideField label={t("ticket.labels")}>
            <LabelsEditor value={data.labels} onSave={(labels) => patch({ labels })} canWrite={canWrite} />
          </SideField>
          <div className="space-y-1 border-t border-line pt-3 text-fg-3">
            <div>{t("ticket.created", { when: timeAgo(data.created_at) })}</div>
            <div>{t("ticket.updated", { when: timeAgo(data.updated_at) })}</div>
          </div>
          {canWrite && (
            <Button variant="danger" size="sm" icon={<Trash2 className="size-3.5" />} onClick={remove}>
              {t("common.delete")}
            </Button>
          )}
          <ErrorText error={error} />
        </aside>
      </div>
      {viewing !== null && (
        <Lightbox
          images={images.map((a) => ({ src: `/api/attachments/${a.id}`, alt: a.filename, video: isVideoFile(a) }))}
          start={Math.max(
            images.findIndex((a) => a.id === viewing),
            0,
          )}
          onClose={() => setViewing(null)}
        />
      )}
    </Modal>
  );
}

function UserAction({ taskId, action }: { taskId: number; action: string }) {
  const { t } = useTranslation();
  const { project } = useShell();
  const qc = useQueryClient();
  const [note, setNote, clearNote] = useDraft(`user-action:${taskId}`);
  const [error, setError] = useState<unknown>(null);

  const [busy, setBusy] = useState(false);

  const done = async () => {
    if (busy) return; // one click tells the manager; the button shows that it is on its way
    setError(null);
    setBusy(true);
    try {
      await api.post(`/tasks/${taskId}/user-action/done`, { note });
      clearNote();
      // The ticket no longer waits: shown at once, not after the ticket has been loaded again.
      qc.setQueryData<TaskDetail>(qk.task(taskId), (old) => (old ? { ...old, user_action: "" } : old));
      qc.invalidateQueries({ queryKey: qk.task(taskId) });
      qc.invalidateQueries({ queryKey: qk.tasks(project.id) });
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <section className="mb-6 space-y-2 rounded-sm bg-you-bg p-3 ring-1 ring-you">
      <h3 className="flex items-center gap-1.5 font-semibold text-you-fg">
        <UserRound className="size-4" />
        {t("ticket.userAction")}
      </h3>
      <p className="whitespace-pre-wrap">
        <TicketText text={action} />
      </p>
      <Textarea rows={2} aria-label={t("ticket.userActionNote")} placeholder={t("ticket.userActionNote")} value={note} onChange={(e) => setNote(e.target.value)} />
      <Button variant="primary" size="sm" icon={<Check className="size-3.5" />} loading={busy} onClick={done}>
        {t("ticket.userActionDone")}
      </Button>
      <ErrorText error={error} />
    </section>
  );
}

function SideField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="font-medium text-fg-3">{label}</div>
      {children}
    </div>
  );
}

function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <h3 className="caps text-fg-2">{children}</h3>
      {right}
    </div>
  );
}

function TitleEditor({ value, onSave, canWrite }: { value: string; onSave: (v: string) => void; canWrite: boolean }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  if (!canWrite) return <span className="min-w-0 flex-1 px-1 py-0.5">{value}</span>;
  return (
    <input
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft.trim() && draft !== value && onSave(draft.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      className="min-w-0 flex-1 rounded-sm bg-transparent px-1 py-0.5 outline-none hover:bg-sunk focus:bg-sunk"
    />
  );
}

type CustomerBody = { customer_title?: string; customer_description?: string; customer_texts?: Record<string, { title?: string; description?: string }> };

/** The ticket's public texts, one tab per customer language of the project (the default first). With one
 * language there are no tabs. Tabs without a title are marked: customers get the default language there. */
function CustomerTexts({ task, languages, onSave, canWrite }: { task: TaskDetail; languages: string[]; onSave: (body: CustomerBody) => void; canWrite: boolean }) {
  const { t, i18n } = useTranslation();
  const name = (code: string) => languageName(code, i18n.language);
  const [lang, setLang] = useState(languages[0]);
  const active = languages.includes(lang) ? lang : languages[0];
  const textsOf = (code: string) => (code === languages[0] ? { title: task.customer_title, description: task.customer_description } : { title: task.customer_texts?.[code]?.title ?? "", description: task.customer_texts?.[code]?.description ?? "" });
  const missing = task.customer_title ? languages.filter((code) => !textsOf(code).title.trim()) : [];
  const save = (field: "title" | "description", value: string) =>
    onSave(active === languages[0] ? { [`customer_${field}`]: value } : { customer_texts: { [active]: { [field]: value } } });
  const { title, description } = textsOf(active);
  const written = languages.some((code) => textsOf(code).title || textsOf(code).description);
  const [expanded, setExpanded] = useState(written);
  const open = expanded || written;
  if (!canWrite && !written) return null;
  return (
    <section className="space-y-2" data-part="customer-texts">
      <SectionTitle
        right={
          (languages.length === 1 || !open) && (
            <span className="flex min-w-0 flex-wrap justify-end gap-1">
              {languages.map((code) => (
                <Badge key={code}>{name(code)}</Badge>
              ))}
            </span>
          )
        }
      >
        <button type="button" aria-expanded={open} disabled={written} onClick={() => setExpanded(!expanded)} className="caps inline-flex items-center gap-1 text-fg-2 hover:text-fg disabled:hover:text-fg-2">
          {!written && (open ? <ChevronDown className="size-3.5 text-fg-3" /> : <ChevronRight className="size-3.5 text-fg-3" />)}
          {t("ticket.customerSection")}
        </button>
      </SectionTitle>
      {open && languages.length > 1 && (
        <div className="scroll-thin -mx-1 overflow-x-auto px-1">
          <Segmented
            aria-label={t("ticket.customerLanguage")}
            value={active}
            onChange={setLang}
            options={languages.map((code) => ({
              value: code,
              label: (
                <span className="inline-flex items-center gap-1 whitespace-nowrap" title={missing.includes(code) ? t("ticket.customerMissingTab") : undefined}>
                  {name(code)}
                  {code === languages[0] && <span className="opacity-70">· {t("customerLanguages.default")}</span>}
                  {missing.includes(code) && <CircleDashed className="size-3" aria-label={t("ticket.customerMissingTab")} />}
                </span>
              ),
            }))}
          />
        </div>
      )}
      {open && <CustomerFields key={active} language={name(active)} title={title} description={description} onSave={save} canWrite={canWrite} />}
      {open && missing.length > 0 && languages.length > 1 && <p className="text-fg-3">{t("ticket.customerMissing", { languages: missing.map(name).join(", ") })}</p>}
      {open && canWrite && <p className="text-fg-3">{t(languages.length > 1 && active === languages[0] ? "ticket.customerDefaultHint" : "ticket.customerHint")}</p>}
    </section>
  );
}

function CustomerFields({ language, title, description, onSave, canWrite }: { language: string; title: string; description: string; onSave: (field: "title" | "description", value: string) => void; canWrite: boolean }) {
  const { t } = useTranslation();
  const [draftTitle, setDraftTitle] = useState(title);
  const [draftDescription, setDraftDescription] = useState(description);
  useEffect(() => setDraftTitle(title), [title]);
  useEffect(() => setDraftDescription(description), [description]);
  if (!canWrite) {
    return (
      <div className="space-y-1">
        {title && <p className="font-medium">{title}</p>}
        {description && <p className="whitespace-pre-wrap">{description}</p>}
      </div>
    );
  }
  return (
    <>
      <Input
        aria-label={t("ticket.customerTitle")}
        placeholder={t("ticket.customerTitleIn", { language })}
        maxLength={150}
        value={draftTitle}
        onChange={(e) => setDraftTitle(e.target.value)}
        onBlur={() => draftTitle !== title && onSave("title", draftTitle)}
      />
      <Textarea
        aria-label={t("ticket.customerDescription")}
        placeholder={t("ticket.customerDescriptionIn", { language })}
        rows={2}
        maxLength={400}
        value={draftDescription}
        onChange={(e) => setDraftDescription(e.target.value)}
        onBlur={() => draftDescription !== description && onSave("description", draftDescription)}
      />
    </>
  );
}

function DescriptionEditor({ draftKey, value, onSave, canWrite }: { draftKey: string; value: string; onSave: (v: string) => void; canWrite: boolean }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useDraft(canWrite ? draftKey : null, value);
  // A draft left from before opens the editor again.
  const [editing, setEditing] = useState(!value || draft !== value);
  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing, setDraft]);

  return (
    <section>
      <SectionTitle
        right={
          !editing &&
          canWrite && (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              {t("common.edit")}
            </Button>
          )
        }
      >
        {t("ticket.description")}
      </SectionTitle>
      {editing && canWrite ? (
        <div className="space-y-2">
          <Textarea autoFocus={!!value} rows={8} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t("ticket.descriptionPlaceholder")} className="font-mono" />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              onClick={() => {
                onSave(draft);
                setEditing(false);
              }}
            >
              {t("common.save")}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setDraft(value);
                setEditing(!value);
              }}
            >
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      ) : (
        <div onDoubleClick={canWrite ? () => setEditing(true) : undefined} className="rounded-sm">
          <Markdown>{value}</Markdown>
        </div>
      )}
    </section>
  );
}

function LabelsEditor({ value, onSave, canWrite }: { value: string[]; onSave: (v: string[]) => void; canWrite: boolean }) {
  const { t } = useTranslation();
  const { project } = useShell();
  const [draft, setDraft] = useState("");
  const add = () => {
    const label = draft.trim();
    if (label && !value.includes(label)) onSave([...value, label]);
    setDraft("");
  };
  return (
    <div className="space-y-1.5">
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {value.map((label) => (
            <span key={label} className="inline-flex items-center gap-1 rounded-sm py-px pr-0.5 pl-1.5 ring-1 ring-line-2 ring-inset">
              <Label colors={project.label_colors} label={label} className="text-fg" />
              {canWrite && (
                <button type="button" onClick={() => onSave(value.filter((l) => l !== label))} aria-label={t("common.remove")} className="rounded-sm p-0.5 text-fg-3 hover:bg-sunk hover:text-fg">
                  <X className="size-3.5" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {canWrite && <Input value={draft} placeholder={t("ticket.addLabel")} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} onBlur={add} />}
    </div>
  );
}

const assignmentTone = { queued: "amber", running: "indigo", done: "green", failed: "red", cancelled: "zinc" } as const;

function Assignments({ assignments, canWrite }: { assignments: Assignment[]; canWrite: boolean }) {
  const { t } = useTranslation();
  const { agentById, openChat } = useShell();
  const [open, setOpen] = useState<number | null>(null);
  if (!assignments.length) return null;
  return (
    <section>
      <SectionTitle>{t("ticket.assignments")}</SectionTitle>
      <ul className="divide-y divide-line rounded-sm ring-1 ring-line">
        {assignments.map((a) => {
          const agent = agentById(a.agent_id);
          const expanded = open === a.id;
          return (
            <li key={a.id}>
              <button onClick={() => setOpen(expanded ? null : a.id)} className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-sunk">
                {expanded ? <ChevronDown className="size-3.5 text-fg-3" /> : <ChevronRight className="size-3.5 text-fg-3" />}
                {agent && <AgentDot color={agent.color} status={a.status === "running" ? "running" : undefined} />}
                <span className="font-medium">{agent?.name ?? "?"}</span>
                <span className="min-w-0 flex-1 truncate text-fg-2">{a.title}</span>
                {a.effort && (
                  <Badge tone="sky" className="hidden sm:inline-flex">
                    {t("ticket.effort", { effort: a.effort })}
                  </Badge>
                )}
                <Badge tone={assignmentTone[a.status]}>{t(`assignment.${a.status}`)}</Badge>
              </button>
              {expanded && (
                <div className="space-y-3 border-t border-line bg-sunk px-4 py-3">
                  <div>
                    <div className="mb-1 font-medium text-fg-3">{t("ticket.instructions")}</div>
                    <Markdown>{a.instructions}</Markdown>
                  </div>
                  {a.result && (
                    <div>
                      <div className="mb-1 font-medium text-fg-3">{t("ticket.result")}</div>
                      <Markdown>{a.result}</Markdown>
                    </div>
                  )}
                  {a.scratchpad && (
                    <details>
                      <summary className="cursor-pointer text-xs text-fg-3">{t("ticket.scratchpad", { chars: a.scratchpad.length })}</summary>
                      <div className="mt-1 text-xs">
                        <Markdown>{a.scratchpad}</Markdown>
                      </div>
                    </details>
                  )}
                  <div className="flex gap-2">
                    {agent && (
                      <Button size="sm" icon={<Bot className="size-3.5" />} onClick={() => openChat(agent.id)}>
                        {t("ticket.openAgentChat")}
                      </Button>
                    )}
                    {canWrite && (a.status === "queued" || a.status === "running") && (
                      <Button size="sm" variant="danger" onClick={() => api.post(`/assignments/${a.id}/cancel`)}>
                        {t("common.cancel")}
                      </Button>
                    )}
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const OLDER_PAGE = 50;

function mergeComments(a: Comment[], b: Comment[]) {
  const byId = new Map(a.map((c) => [c.id, c]));
  for (const c of b) byId.set(c.id, c);
  return [...byId.values()].sort((x, y) => x.id - y.id);
}

// The ticket only carries the newest comments; older ones are paged in on demand.
function Comments({ taskId, comments, total, canWrite }: { taskId: number; comments: Comment[]; total: number; canWrite: boolean }) {
  const { t } = useTranslation();
  const { agentById, project } = useShell();
  const qc = useQueryClient();
  const [body, setBody, clearBody] = useDraft(`comment:${taskId}`);
  const field = useRef<HTMLTextAreaElement>(null);
  const picker = useMentionPicker(project.id, body, setBody, field);
  useReadMentionsOf({ taskId });
  const [error, setError] = useState<string | null>(null);
  const [seen, setSeen] = useState<Comment[]>([]);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const shown = mergeComments(seen, comments);
  const hidden = Math.max(0, total - shown.length);

  // Keep comments that slide out of the newest page while the ticket is open.
  useEffect(() => setSeen((prev) => mergeComments(prev, comments)), [comments]);

  const loadOlder = async () => {
    if (!shown.length) return;
    setLoadingOlder(true);
    try {
      const older = await api.get<Comment[]>(`/tasks/${taskId}/comments?before=${shown[0].id}&limit=${OLDER_PAGE}`);
      setSeen((prev) => mergeComments(prev, older));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoadingOlder(false);
    }
  };

  const send = async () => {
    if (!body.trim()) return;
    try {
      await api.post(`/tasks/${taskId}/comments`, { body });
      clearBody();
      qc.invalidateQueries({ queryKey: qk.task(taskId) });
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <section>
      <SectionTitle>{t("ticket.comments")}</SectionTitle>
      <div className="space-y-3">
        {hidden > 0 && (
          <Button size="sm" variant="ghost" className="w-full" loading={loadingOlder} onClick={loadOlder}>
            {t("ticket.olderComments", { count: Math.min(hidden, OLDER_PAGE), total: hidden })}
          </Button>
        )}
        {shown.map((c) => {
          const agent = agentById(c.author_agent_id);
          const author = c.author_kind === "agent" ? (agent?.name ?? t("ticket.agent")) : c.author_kind === "system" ? t("ticket.system") : t("ticket.you");
          return (
            <div key={c.id} className="flex gap-2.5">
              {agent ? (
                <AgentSquare agent={agent} className="mt-px" />
              ) : (
                <span className="mt-px inline-flex size-[18px] shrink-0 items-center justify-center rounded-sm text-[11.5px] leading-none font-extrabold ring-1 ring-line-2 ring-inset">
                  {author[0]?.toUpperCase()}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <div className="text-fg-3">
                  <span className="font-medium text-fg">{author}</span> · {timeAgo(c.created_at)}
                </div>
                <Markdown>{c.body}</Markdown>
              </div>
            </div>
          );
        })}
        {canWrite ? (
        <div className="relative flex items-end gap-2">
          {picker.menu}
          <Textarea
            ref={field}
            rows={2}
            value={body}
            placeholder={t("ticket.commentPlaceholder")}
            onChange={(e) => {
              setBody(e.target.value);
              picker.track();
            }}
            onSelect={picker.track}
            onKeyDown={(e) => {
              if (picker.onKeyDown(e)) return;
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
            }}
          />
          <IconButton label={t("ticket.sendComment")} onClick={send} className="mb-1">
            <Send className="size-4" />
          </IconButton>
        </div>
        ) : (
          <p className="text-fg-3">{t("members.readOnlyShort")}</p>
        )}
        {error && <p className="text-you-fg">{error}</p>}
      </div>
    </section>
  );
}
