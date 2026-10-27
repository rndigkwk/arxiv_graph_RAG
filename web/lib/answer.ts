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

/** 예측 패널의 시드. 관련 논문을 고른 시드의 첫 편입니다(Streamlit 패널과 같은 규칙, ADR-038). */
export function forecastSeed(answer: Answer): string | null {
  const calls = answer.tool_calls.filter((c) => c.name === "rank_related_papers");
  const output = calls.at(-1)?.output as { seed_ids?: unknown } | undefined;
  const seeds = Array.isArray(output?.seed_ids) ? output.seed_ids : [];
  return typeof seeds[0] === "string" ? seeds[0] : null;
}

/** API에 보낼 앞 문답. 오래된 것부터 버립니다. */
export function historyFor(turns: { question: string; answer: string }[]) {
  return turns.slice(-MAX_HISTORY);
}

export function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}초`;
}

// 노드 이름은 `app.py`의 NODE_LABEL과 같습니다.
export const NODE_LABEL: Record<string, string> = {
  route: "경로 판정",
  generate: "답변 생성",
  collect: "결과 수집",
  check_format: "형식·인용 검사",
  check_conditions: "조건 검사",
  revise: "한 번 수정",
  accept: "통과",
  abstain: "보류",
};
// `generate` 안을 재지 못한 응답(옛 보고서 등)에서 쓰는 말입니다.
export const GENERATE_CONTAINS = "번역·Cypher·벡터·모델 턴 포함";

/** `답변 생성`을 모델·도구·나머지로 가른 한 마디. **합이 맞지 않으면 수를 적지 않습니다** - 틀린 분해를 측정값처럼 읽게 됩니다. */
export function generateSplit(answer: Answer): string {
  const t = answer.generate_trace;
  if (!t?.adds_up || (!t.tool_calls && !t.model_turns)) return "";
  const parts = [`모델 ${t.model_seconds.toFixed(1)}초 (${t.model_turns}턴)`, `도구 ${t.tools_seconds.toFixed(1)}초 (${t.tool_calls}회)`];
  if (t.unattributed_seconds >= 0.05) parts.push(`이름 못 붙임 ${t.unattributed_seconds.toFixed(1)}초`);
  // 나머지는 잰 값이 아니라 뺀 값입니다. 이름으로 구분합니다.
  parts.push(`그 외 ${Math.max(t.other_seconds, 0).toFixed(1)}초`);
  return parts.join(" · ");
}

export type StageRow = { node: string; seconds: number; visits: number; note: string };

/** 노드별 시간, 느린 것부터. **0초도 지우지 않습니다** - 공짜라는 것이 분해의 결론입니다. 마지막 줄이 합계입니다. */
export function stageRows(answer: Answer): StageRow[] {
  const trace = answer.graph_trace;
  if (!trace || !Object.keys(trace.seconds).length) return [];
  const inside = generateSplit(answer);
  const rows = Object.entries(trace.seconds)
    .sort((a, b) => b[1] - a[1])
    .map(([node, value]) => ({
      node: NODE_LABEL[node] ?? node,
      seconds: value,
      visits: trace.visits[node] ?? 1,
      note: node === "generate" ? inside || GENERATE_CONTAINS : "",
    }));
  const visits = Object.values(trace.visits).reduce((a, b) => a + b, 0) || rows.length;
  return [...rows, { node: "합계", seconds: trace.total_seconds, visits, note: "" }];
}

/** 재시도는 느린 답의 흔한 이유입니다. 숨기면 합계만 보고 모델을 의심하게 됩니다. */
export const attemptsOf = (answer: Answer) => answer.graph_trace?.attempts ?? 1;
