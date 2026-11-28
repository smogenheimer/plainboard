/** What a person typed into the board of ideas, on its way to the server (#161). The field is free again at
 * once; inputs are sent one after the other in the order typed (the server reads them in that order). One
 * that fails stays here with its error until it is sent again or removed, and holds up none of the others.
 * Kept per project outside the view, so that leaving the page loses nothing that is still being sent. */

export interface Outgoing<F = unknown> {
  key: number;
  text: string;
  files: F[];
  state: "sending" | "failed";
  error: string;
}

export class Outbox<F = unknown> {
  items: Outgoing<F>[] = [];
  private chain: Promise<void> = Promise.resolve();
  private next = 1;
  private listeners = new Set<() => void>();
  private send: (item: Outgoing<F>) => Promise<void>;
  private describe: (err: unknown) => string;

  constructor(send: (item: Outgoing<F>) => Promise<void>, describe: (err: unknown) => string = String) {
    this.send = send;
    this.describe = describe;
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  snapshot = () => this.items;

  /** Queued at once; returns when it was sent or failed. */
  add(text: string, files: F[] = []): Promise<void> {
    const item: Outgoing<F> = { key: this.next++, text, files, state: "sending", error: "" };
    this.set([...this.items, item]);
    return this.run(item);
  }

  /** A failed one again, after those still waiting. */
  retry(key: number): Promise<void> {
    const item = this.items.find((i) => i.key === key);
    if (!item || item.state !== "failed") return Promise.resolve();
    const again = { ...item, state: "sending" as const, error: "" };
    this.set(this.items.map((i) => (i.key === key ? again : i)));
    return this.run(again);
  }

  remove(key: number) {
    this.set(this.items.filter((i) => i.key !== key || i.state === "sending"));
  }

  /** Sent ones leave; once all are, everything after them was sent too. */
  idle(): Promise<void> {
    return this.chain;
  }

  private run(item: Outgoing<F>): Promise<void> {
    this.chain = this.chain.then(async () => {
      try {
        await this.send(item);
        this.set(this.items.filter((i) => i.key !== item.key));
      } catch (err) {
        this.set(this.items.map((i) => (i.key === item.key ? { ...i, state: "failed", error: this.describe(err) } : i)));
      }
    });
    return this.chain;
  }

  private set(items: Outgoing<F>[]) {
    this.items = items;
    this.listeners.forEach((l) => l());
  }
}
