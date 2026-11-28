// The queue of inputs to the board of ideas (#161). Run: npm test (Node strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { Outbox, type Outgoing } from "../src/features/ideas/outbox.ts";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("inputs typed in a row are sent one after the other, in order, without waiting to be typed", async () => {
  const sent: string[] = [];
  let open = 0;
  let most = 0;
  const box = new Outbox<never>(async (item) => {
    open += 1;
    most = Math.max(most, open);
    await tick();
    sent.push(item.text);
    open -= 1;
  });
  for (let n = 0; n < 10; n++) void box.add(`idea ${n}`);
  assert.equal(box.items.length, 10);
  assert.ok(box.items.every((i) => i.state === "sending"));
  await box.idle();
  assert.deepEqual(sent, Array.from({ length: 10 }, (_, n) => `idea ${n}`));
  assert.equal(most, 1);
  assert.deepEqual(box.items, []);
});

test("a failed input stays with its error, the others go on, and it can be sent again or removed", async () => {
  let down = true;
  const sent: string[] = [];
  const box = new Outbox<string>(
    async (item: Outgoing<string>) => {
      if (item.text === "bad" && down) throw new Error("offline");
      sent.push(item.text);
    },
    (err) => (err as Error).message,
  );
  void box.add("one");
  void box.add("bad", ["photo.png"]);
  void box.add("three");
  await box.idle();
  assert.deepEqual(sent, ["one", "three"]);
  assert.equal(box.items.length, 1);
  const [failed] = box.items;
  assert.deepEqual([failed.text, failed.files, failed.state, failed.error], ["bad", ["photo.png"], "failed", "offline"]);

  down = false;
  await box.retry(failed.key);
  assert.deepEqual(sent, ["one", "three", "bad"]);
  assert.deepEqual(box.items, []);

  down = true;
  await box.add("bad");
  box.remove(box.items[0].key);
  assert.deepEqual(box.items, []);
});

test("listeners hear every change", async () => {
  const box = new Outbox<never>(async () => {});
  let heard = 0;
  const stop = box.subscribe(() => (heard += 1));
  await box.add("x");
  assert.equal(heard, 2); // queued, sent
  stop();
  await box.add("y");
  assert.equal(heard, 2);
});
