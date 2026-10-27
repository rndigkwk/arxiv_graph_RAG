// 답 하나를 화면에 펴기 위한 순수 계산. 테스트가 이것만 봅니다(lib/answer.test.ts).

import type { Answer, Route } from "./api";

export const MAX_HISTORY = 6; // src/api.py의 MAX_HISTORY_TURNS와 같습니다.

/** 화면이 보여 줄 경로 이름. 도구가 실제로 쓰인 것을 봅니다(시작 전 판정이 아니라). */
export function usedPath(answer: Answer): "그래프 경로" | "넓은 경로" {
  return answer.tool_calls.some((c) => c.name === "search_papers") ? "넓은 경로" : "그래프 경로";
}

export function routeLine(route: Route): string {
  const head = route.name === "graph" ? "→ 그래프 경로 — 저장된 관계만 묻는 질문으로 봅니다" : "→ 넓은 경로 — 의미 검색도 함께 씁니다";
  return route.cues.length ? `${head} · 단서 ${route.cues.join(", ")}` : head;
}

/** 근거로 쓴 논문 다음에 이어서 읽을 논문. 겹치면 한 번만, 그래프에 그릴 수 있는 만큼만. */
export function graphIds(answer: Answer, limit = 20): string[] {
  const ids = [...answer.evidence_ids, ...answer.related.map((r) => r.arxiv_id)];
  return [...new Set(ids)].slice(0, limit);
}

/** 근거 중 몇 편이 실제로 조회 결과(그래프 행이나 의미 검색)에 있었는가. 답이 지어낸 근거를 드러냅니다. */
export function citedEvidence(answer: Answer): { found: number; total: number } {
  const seen = JSON.stringify([answer.rows, answer.papers]);
  const found = answer.evidence_ids.filter((id) => seen.includes(id)).length;
  return { found, total: answer.evidence_ids.length };
}

/** API에 보낼 앞 문답. 오래된 것부터 버립니다. */
export function historyFor(turns: { question: string; answer: string }[]) {
  return turns.slice(-MAX_HISTORY);
}

export function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}초`;
}
