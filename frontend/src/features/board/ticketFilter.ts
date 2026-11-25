import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../../api/client";
import type { ProjectSearch, Task } from "../../api/types";
import { useDebounced } from "../../lib/hooks";
import { DEBOUNCE_MS, MIN_SEMANTIC } from "../layout/Palette";

/** The board's and the archive's "Search tickets…" (#133): at once by title, label and number, and a moment
 * after the last key also like the search (Ctrl+K), by meaning and words, tickets and their text files (#157). Semantic search off or
 * not there: the words alone. Ranked: the ticket with that number first, then the search's order. */
export function useTicketFilter(projectId: number, filter: string, tasks: Task[] | undefined): { matches: Task[] } {
  const q = filter.trim();
  const number = /^#?\d+$/.test(q);
  const debounced = useDebounced(q, DEBOUNCE_MS);
  const asks = debounced.length >= MIN_SEMANTIC && !/^#?\d+$/.test(debounced);
  const search = useQuery({
    queryKey: ["search", projectId, debounced, "tickets"],
    queryFn: () => api.get<ProjectSearch>(`/projects/${projectId}/search?q=${encodeURIComponent(debounced)}&kinds=ticket&kinds=file&close=true&limit=50`),
    enabled: asks,
    staleTime: 30_000,
  });
  const hits = !number && asks && debounced === q ? search.data?.hits : undefined;

  const matches = useMemo(() => {
    const all = tasks ?? [];
    const needle = q.toLowerCase();
    if (!needle) return all;
    const nr = needle.replace(/^#/, "");
    const local = all.filter((task) => task.title.toLowerCase().includes(needle) || nr === String(task.number) || task.labels.some((l) => l.toLowerCase().includes(needle)));
    const byId = new Map(all.map((task) => [task.id, task]));
    const found = (hits ?? []).flatMap((h) => (h.task_id !== null && byId.has(h.task_id) ? [byId.get(h.task_id)!] : []));
    const out = [...local.filter((task) => nr === String(task.number)), ...found, ...local];
    return [...new Set(out)];
  }, [tasks, q, hits]);

  return { matches };
}
