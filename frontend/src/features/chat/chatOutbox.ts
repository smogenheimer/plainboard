import { useEffect, useSyncExternalStore } from "react";
import { ApiError, api, errorMessage } from "../../api/client";

/** Chat messages on their way to the server (#174). Each is kept in localStorage until the server has
 * taken it, so a reload, a closed tab or a server that restarts loses nothing: while the server can't be
 * reached it is sent again every few seconds, in the order written. Its key makes the server take it only
 * once, also when the first try arrived and only the answer got lost. One the server turns down goes back
 * to the field (`takeRejected`). Kept outside the view: switching agents doesn't stop the sending.
 * One that shows up in the chat has arrived, whatever became of its request (`settle`, #179). Tabs share
 * the stored list, and only one of them sends at a time. */

export interface OutgoingChat {
  key: string;
  text: string;
  attachment_ids: number[];
  /** Set when the server turned it down. */
  error?: string;
}

const RETRY_MS = 3000;

const unreachable = (err: unknown) => !(err instanceof ApiError) || err.status === 502 || err.status === 503 || err.status === 504;

const newKey = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

class ChatOutbox {
  items: OutgoingChat[];
  /** The last try could not reach the server: the waiting ones are shown. */
  waiting = false;
  private running = false;
  /** The list is kept in localStorage; when that can't be written, here only. */
  private stored = true;
  private listeners = new Set<() => void>();

  constructor(
    private storageKey: string,
    private agentId: number,
    private onSent: () => void,
  ) {
    const items = load(storageKey);
    this.stored = items !== null;
    this.items = items ?? [];
    window.addEventListener("storage", (e) => {
      if (e.key !== this.storageKey || !this.stored) return;
      this.items = load(this.storageKey) ?? this.items; // another tab added, sent or took back one
      this.notify();
      if (this.items.length) this.start(); // it waits for that tab's sending, and takes over if it closes
    });
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  snapshot = () => this.items;

  add(text: string, attachment_ids: number[]) {
    this.update((items) => [...items, { key: newKey(), text, attachment_ids }]);
    this.start();
  }

  remove(key: string) {
    this.update((items) => items.filter((i) => i.key !== key));
  }

  /** The server has these (the chat shows them): done, also while the request for one is still open. */
  settle(keys: Set<string>) {
    if (this.items.some((i) => keys.has(i.key))) this.update((items) => items.filter((i) => !keys.has(i.key)));
  }

  /** The first one the server turned down, taken out to go back into the field. */
  takeRejected(): OutgoingChat | undefined {
    const item = this.items.find((i) => i.error);
    if (item) this.remove(item.key);
    return item;
  }

  start() {
    if (this.running) return;
    this.running = true;
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    // One tab at a time: the same message is never on its way twice.
    void (locks ? locks.request(`plainboard.chatout:${this.storageKey}`, () => this.loop()) : this.loop()).catch(() => {
      this.running = false;
    });
  }

  private async loop() {
    try {
      for (let item = this.next(); item; item = this.next()) {
        try {
          await api.post(`/agents/${this.agentId}/messages`, { text: item.text, attachment_ids: item.attachment_ids, client_key: item.key });
          this.waiting = false;
          this.remove(item.key);
          this.onSent();
        } catch (err) {
          if (!unreachable(err)) {
            const key = item.key;
            this.update((items) => items.map((i) => (i.key === key ? { ...i, error: errorMessage(err) } : i)));
            continue;
          }
          if (!this.waiting) {
            this.waiting = true;
            this.notify();
          }
          await new Promise((resolve) => setTimeout(resolve, RETRY_MS));
        }
      }
    } finally {
      this.running = false;
      if (this.waiting) {
        this.waiting = false;
        this.notify();
      }
    }
  }

  private next() {
    if (this.stored) {
      this.items = load(this.storageKey) ?? this.items; // what another tab sent meanwhile is not sent again
      this.notify();
    }
    return this.items.find((i) => !i.error);
  }

  /** Changes the latest stored list, not this tab's copy of it: another tab may have changed it. */
  private update(change: (items: OutgoingChat[]) => OutgoingChat[]) {
    const items = change((this.stored && load(this.storageKey)) || this.items);
    this.items = items;
    try {
      if (items.length) localStorage.setItem(this.storageKey, JSON.stringify(items));
      else localStorage.removeItem(this.storageKey);
    } catch {
      this.stored = false; // storage full or blocked: it lives as long as the app is open
    }
    this.notify();
  }

  private notify() {
    this.listeners.forEach((l) => l());
  }
}

/** The stored list; null when storage can't be read. */
function load(storageKey: string): OutgoingChat[] | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(storageKey);
  } catch {
    return null;
  }
  try {
    const items = JSON.parse(raw ?? "[]");
    return Array.isArray(items) ? items.filter((i) => i && typeof i.key === "string" && typeof i.text === "string") : [];
  } catch {
    return [];
  }
}

const outboxes = new Map<string, ChatOutbox>();

/** The outbox of one agent's chat for the signed-in user. Stored under the drafts' prefix, so that
 * signing out clears it like the drafts (`clearDrafts`). */
export function useChatOutbox(userId: number, agentId: number, onSent: () => void) {
  const storageKey = `plainboard.draft.${userId}.chatout:${agentId}`;
  let outbox = outboxes.get(storageKey);
  if (!outbox) {
    outbox = new ChatOutbox(storageKey, agentId, onSent);
    outboxes.set(storageKey, outbox);
  }
  const items = useSyncExternalStore(outbox.subscribe, outbox.snapshot);
  useEffect(() => {
    outbox.start(); // what an earlier visit (or the last page before a reload) left unsent
  }, [outbox]);
  return { outbox, items, waiting: outbox.waiting };
}
