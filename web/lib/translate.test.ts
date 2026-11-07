import { afterEach, describe, expect, it, vi } from "vitest";
import { translateStream } from "./api";

// 번역을 흘려 받습니다(ADR-114). API가 아직 흘려 보내지 않으면 한 번에 받는 길로 돌아갑니다.

function sseBody(text: string): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, 20));
      controller.enqueue(bytes.slice(20));
      controller.close();
    },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("translateStream", () => {
  it("hands each partial over and returns the final text", async () => {
    const body =
      'event: partial\ndata: {"text": "그래프"}\n\n' +
      'event: partial\ndata: {"text": "그래프에 있는"}\n\n' +
      'event: final\ndata: {"text": "그래프에 있는 논문입니다."}\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(sseBody(body), { status: 200 })));
    const seen: string[] = [];
    const text = await translateStream("t", (partial) => seen.push(partial));
    expect(seen).toEqual(["그래프", "그래프에 있는"]);
    expect(text).toBe("그래프에 있는 논문입니다.");
  });

  it("falls back to the one-shot translation before the API streams", async () => {
    const fetch = vi.fn(async (url: string) =>
      url.endsWith("/api/translate/stream")
        ? new Response("not found", { status: 404 })
        : new Response(JSON.stringify({ text: "한 번에" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetch);
    expect(await translateStream("t", () => {})).toBe("한 번에");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("throws on a limit or a failed stream so the screen keeps the English answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 429, headers: { "retry-after": "30" } })));
    await expect(translateStream("t", () => {})).rejects.toMatchObject({ status: 429, retryAfter: 30 });

    const failed = 'event: error\ndata: {"message": "번역하지 못했습니다."}\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(sseBody(failed), { status: 200 })));
    await expect(translateStream("t", () => {})).rejects.toThrow("번역하지 못했습니다.");
  });
});
