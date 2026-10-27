// POST로 여는 SSE를 읽습니다. 브라우저의 EventSource는 GET만 보내므로 fetch 스트림을 직접 나눕니다.
// 모양은 API(src/api.py의 `sse`)가 정합니다: "event: <종류>\ndata: <JSON>\n\n".

export type SseEvent = { event: string; data: unknown };

/** 받은 조각을 이어 붙여 끝난 이벤트만 꺼냅니다. 남은 반쪽은 `rest`로 돌려줍니다. */
export function takeEvents(buffer: string): { events: SseEvent[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: SseEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length) events.push({ event, data: JSON.parse(data.join("\n")) });
  }
  return { events, rest };
}

export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  // stream: true가 조각 경계에서 잘린 한글(여러 바이트)을 다음 조각까지 들고 있습니다.
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const { events, rest } = takeEvents(buffer + decoder.decode(value, { stream: true }));
    buffer = rest;
    yield* events;
  }
}
