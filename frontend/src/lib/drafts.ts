import { useCallback, useEffect, useState } from "react";
import { useMe } from "../api/queries";

// What someone has typed and not sent yet stays when they go to another view and come back, and
// after a reload: in localStorage, per signed-in user and per place (`chat:<agentId>`, …).
// Files can't go there; they stay in memory while the app is open.

const PREFIX = "plainboard.draft.";

const storageKey = (userId: number, key: string) => `${PREFIX}${userId}.${key}`;

function read(full: string): string {
  try {
    return localStorage.getItem(full) ?? "";
  } catch {
    return "";
  }
}

function write(full: string, value: string) {
  try {
    if (value.trim()) localStorage.setItem(full, value);
    else localStorage.removeItem(full);
  } catch {
    /* storage full or blocked: the draft lives as long as the view */
  }
}

/** A text field's draft: kept per user under `key`, back when the field shows again. `clear()` after
 * it was sent or saved. A `null` key keeps nothing (e.g. while editing something that saves itself). */
export function useDraft(key: string | null, initial = "") {
  const me = useMe();
  const full = key && me ? storageKey(me.id, key) : null;
  const [value, setValue] = useState(() => (full && read(full)) || initial);
  const [shown, setShown] = useState(full);
  // Another place (another agent's chat in the same panel): its own draft.
  if (shown !== full) {
    setShown(full);
    const next = (full && read(full)) || initial;
    if (next !== value) setValue(next);
  }
  useEffect(() => {
    // Unchanged from where it started is no draft: what was saved shows as saved next time.
    if (full) write(full, value === initial ? "" : value);
  }, [full, value, initial]);
  const clear = useCallback(() => {
    if (full) write(full, "");
    setValue("");
  }, [full]);
  return [value, setValue, clear] as const;
}

const fileDrafts = new Map<string, File[]>();

/** Files picked for a message and not sent yet: kept in memory, per user and place, while the app is open. */
export function useDraftFiles(key: string) {
  const me = useMe();
  const full = storageKey(me?.id ?? 0, key);
  const [files, setFiles] = useState<File[]>(() => fileDrafts.get(full) ?? []);
  const [shown, setShown] = useState(full);
  if (shown !== full) {
    setShown(full);
    const next = fileDrafts.get(full) ?? [];
    if (next !== files) setFiles(next);
  }
  const set = useCallback(
    (update: File[] | ((cur: File[]) => File[])) => {
      const next = typeof update === "function" ? update(fileDrafts.get(full) ?? []) : update;
      if (next.length) fileDrafts.set(full, next);
      else fileDrafts.delete(full);
      setFiles(next);
    },
    [full],
  );
  return [files, set] as const;
}

/** On sign-out: no draft stays for the next one on this browser. */
export function clearDrafts() {
  fileDrafts.clear();
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith(PREFIX)) localStorage.removeItem(k);
  } catch {
    /* nothing to clear */
  }
}
