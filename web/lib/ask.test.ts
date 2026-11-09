import { afterEach, describe, expect, it, vi } from "vitest";
import { type AskEvent, ask, CUT_OFF } from "./api";

// 질문 스트림은 final·abstain·error 하나로 끝납니다. 끝 이벤트 없이 닫히면 오류로 끝냅니다(ADR-115).

function body(text: string): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
}

async function collect(text: string): Promise<AskEvent[]> {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body(text), { status: 200 })));
  const seen: AskEvent[] = [];
  for await (const event of ask("q", [])) seen.push(event);
  return seen;
}

afterEach(() => vi.unstubAllGlobals());

describe("ask", () => {
  it("ends with the final answer", async () => {
    const seen = await collect(
      'event: partial\ndata: {"answer": "Ye"}\n\nevent: final\ndata: {"answer": "Yes.", "evidence_ids": []}\n\n',
    );
    expect(seen.map((e) => e.kind)).toEqual(["partial", "final"]);
  });

  it("turns a stream cut before its end event into an error", async () => {
    const seen = await collect('event: partial\ndata: {"answer": "Ye"}\n\n');
    expect(seen.at(-1)).toEqual({ kind: "error", message: CUT_OFF });
  });

  it("passes the server's error message through", async () => {
    const seen = await collect('event: error\ndata: {"message": "AI 모델의 사용 한도가 다 되어"}\n\n');
    expect(seen).toEqual([{ kind: "error", message: "AI 모델의 사용 한도가 다 되어" }]);
  });
});
