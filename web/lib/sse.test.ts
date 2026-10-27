import { describe, expect, it } from "vitest";
import { readEvents, takeEvents } from "./sse";

describe("takeEvents", () => {
  it("keeps a half-received event for the next chunk", () => {
    const first = takeEvents('event: partial\ndata: {"answer":"Ye"}\n\nevent: fin');
    expect(first.events).toEqual([{ event: "partial", data: { answer: "Ye" } }]);
    const second = takeEvents(first.rest + 'al\ndata: {"answer":"Yes."}\n\n');
    expect(second.events).toEqual([{ event: "final", data: { answer: "Yes." } }]);
    expect(second.rest).toBe("");
  });

  it("reads CRLF and Korean text", () => {
    const { events } = takeEvents('event: abstain\r\ndata: {"message":"보류"}\r\n\r\n');
    expect(events[0]).toEqual({ event: "abstain", data: { message: "보류" } });
  });
});

describe("readEvents", () => {
  it("splits events across arbitrary byte chunks, even inside a multibyte character", async () => {
    const bytes = new TextEncoder().encode(
      'event: partial\ndata: {"answer":"한"}\n\nevent: final\ndata: {"answer":"한국"}\n\n',
    );
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (let i = 0; i < bytes.length; i += 5) c.enqueue(bytes.slice(i, i + 5));
        c.close();
      },
    });
    const got = [];
    for await (const e of readEvents(body)) got.push(e);
    expect(got.map((e) => e.event)).toEqual(["partial", "final"]);
    expect(got[1].data).toEqual({ answer: "한국" });
  });
});
