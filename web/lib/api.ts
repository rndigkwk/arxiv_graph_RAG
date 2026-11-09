// VM의 FastAPI 층(ADR-081)을 부릅니다. 브라우저가 VM을 직접 부릅니다 - Vercel을 거치면 VM이 보는
// IP가 Vercel 것이 되어 IP별 상한이 한 덩어리가 됩니다.

import { readEvents } from "./sse";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "https://8-230-25-138.sslip.io").replace(/\/$/, "");

export type Route = { name: "graph" | "any"; cues: string[]; vetoes: string[] };
export type ToolCall = { id: string; name: string; args: unknown; status?: string; output?: unknown };
export type Related = { arxiv_id: string; title?: string; score?: number; reason?: string };
// 넓은 경로의 벡터 검색 결과. 저자와 게재일은 없습니다(저장할 때 논문 API에서 읽습니다).
export type VectorHit = {
  arxiv_id: string;
  title?: string;
  abstract?: string;
  score?: number;
  categories?: string[];
  pdf_url?: string;
};
// 노드별 시간(`agent_graph.summarize_trace`)과 `generate` 안쪽의 분해(`tool_timing.summary`).
export type GraphTrace = {
  seconds: Record<string, number>;
  visits: Record<string, number>;
  attempts: number;
  total_seconds: number;
  rejections?: { node: string; stage: string; attempt: number }[];
};
export type GenerateTrace = {
  adds_up: boolean;
  model_seconds: number;
  model_turns: number;
  tools_seconds: number;
  tool_calls: number;
  unattributed_seconds: number;
  other_seconds: number;
};
export type Answer = {
  question: string;
  answer: string;
  evidence_ids: string[];
  tool_calls: ToolCall[];
  cyphers: string[];
  rows: Record<string, unknown>[];
  papers: VectorHit[];
  related: Related[];
  graph_trace?: GraphTrace;
  generate_trace?: GenerateTrace;
};
// `explained`는 `abstain.explain`이 만든 문장입니다. API가 나중에 더한 칸이라 없을 수 있습니다.
export type Abstain = {
  message: string;
  stage?: string;
  reason?: string;
  attempts?: number;
  request_id?: string;
  cyphers?: string[];
  explained?: { headline: string; why: string; what: string; advice: string; stage: string; attempts: number; request_id: string; cyphers: string[] };
};
export type SubgraphPaper = {
  arxiv_id: string;
  title: string;
  primary_category: string;
  authors: string[];
  author_count: number;
  cited_by: number;
};
export type Subgraph = { papers: SubgraphPaper[]; references: { citing: string; cited: string }[] };
export type Turn = { question: string; answer: string };

export type RankBy = "cited" | "pagerank" | "personalized";
export type RankQuery = {
  by: RankBy;
  category?: string;
  includeSecondary: boolean;
  scope: "global" | "subgraph";
  seed?: string;
  skip: number;
};
export type RankedPaper = {
  rank: number;
  score: number;
  arxiv_id: string;
  title: string;
  primary_category: string;
  published: string | null;
  cited_by: number;
};
export type Ranking = { by: RankBy; category: string | null; scope: string; total: number; skip: number; papers: RankedPaper[] };
export type Counts = { papers: number; authors: number; authored: number; references: number };
export type Stats = { counts: Counts; top_categories: { code: string; papers: number }[] };
export type Community = { label: string; papers: number; primary_category: string; secondary_category: string | null; inside: number };
export type Overview = {
  counts: Counts;
  communities: { generated_at: string; mode: string; rows: Community[]; links: { a: string; b: string; citations: number }[] } | null;
};
export type RelatedPaper = {
  arxiv_id: string;
  title: string;
  primary_category: string;
  cited_by: number;
  shared_citers: number | null;
  ranked_by: "cocitation" | "pagerank" | "embedding";
};
export type Paper = {
  arxiv_id: string;
  title: string;
  abstract: string | null;
  primary_category: string;
  categories: string[];
  pdf_url: string | null;
  published: string | null;
  authors: string[];
  cited_by: number;
  references: number;
  bibtex: string;
  related: RelatedPaper[];
};
// 앞으로 함께 인용될 논문(ADR-038). 자체 학습 GBM이 후보를 재정렬합니다.
export type ForecastHit = {
  arxiv_id: string;
  score: number;
  title?: string;
  shared_cocitations: number;
  embedding_cosine: number;
};
export type Forecast = {
  seed_id: string;
  papers: ForecastHit[];
  candidates?: number;
  model?: { digest?: string; snapshot_version?: string; ranking_mrr?: number };
  reason: string;
};
export type CitingPaper = { arxiv_id: string; title: string; primary_category: string; published: string | null; cited_by: number };
export type CitingOrder = "recent" | "cited";
export type SearchHit = { arxiv_id: string; title: string; primary_category: string };
export type SearchResult = { query: string; arxiv_id?: string | null; method?: string; ambiguous?: boolean; candidates: SearchHit[]; note?: string };

export type AskEvent =
  | { kind: "partial"; answer: string }
  | { kind: "final"; answer: Answer }
  | { kind: "abstain"; abstain: Abstain }
  | { kind: "error"; message: string }
  | { kind: "limited"; retryAfter: number };

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter?: number) {
    super(message);
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE + path, init);
  if (!response.ok) {
    const retry = Number(response.headers.get("retry-after")) || undefined;
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {}
    throw new ApiError(response.status, String(detail), retry);
  }
  return response.json() as Promise<T>;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const getRoute = (q: string, signal?: AbortSignal) =>
  json<Route>(`/api/route?q=${encodeURIComponent(q)}`, { signal });
export const getSubgraph = (ids: string[]) => json<Subgraph>("/api/subgraph", post({ arxiv_ids: ids }));
export const getStats = () => json<Stats>("/api/stats");
export const getRanking = (query: string) => json<Ranking>(`/api/rank?${query}`);
export const translate = (text: string) => json<{ text: string }>("/api/translate", post({ text }));

/**
 * 번역을 흘려 받습니다(ADR-114). `onPartial`이 지금까지 옮긴 글을 받고, 끝난 글을 돌려줍니다.
 * API가 아직 흘려 보내지 않으면(404, 배포 전) 한 번에 받는 `/api/translate`로 돌아갑니다.
 */
export async function translateStream(text: string, onPartial: (text: string) => void): Promise<string> {
  const response = await fetch(API_BASE + "/api/translate/stream", post({ text }));
  if (response.status === 404) return (await translate(text)).text;
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, response.statusText, Number(response.headers.get("retry-after")) || undefined);
  }
  for await (const { event, data } of readEvents(response.body)) {
    const d = data as Record<string, unknown>;
    if (event === "partial") onPartial(String(d.text ?? ""));
    else if (event === "final") return String(d.text ?? "");
    else if (event === "error") throw new ApiError(500, String(d.message ?? "번역하지 못했습니다."));
  }
  throw new ApiError(500, "번역이 끝나지 않았습니다.");
}

// 논문 페이지는 서버에서 그립니다. 그래프는 주간 수집 때만 바뀌므로 한 시간 캐시합니다.
// 이 길들은 모델을 부르지 않아 상한이 없고, 그래서 Vercel을 거쳐도 됩니다(ADR-082는 질문·번역 얘기).
export async function getPaper(id: string): Promise<Paper | null> {
  try {
    return await json<Paper>(`/api/papers/${encodeURIComponent(id)}`, { next: { revalidate: 3600 } });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}
// 모델을 부르지만 OpenAI가 아니라 저장된 가중치라 상한 밖입니다. 버튼을 눌러야 부릅니다.
export const getForecast = (id: string) => json<Forecast>(`/api/papers/${encodeURIComponent(id)}/forecast`);
export const getCiting = (id: string, order: CitingOrder, skip: number, limit = 20) =>
  json<{ order: CitingOrder; papers: CitingPaper[] }>(
    `/api/papers/${encodeURIComponent(id)}/citing?order=${order}&skip=${skip}&limit=${limit}`,
  );
// 그래프 소개 화면. 노드 수는 주간 수집 때만 바뀌므로 한 시간 캐시합니다.
export async function getOverview(): Promise<Overview | null> {
  try {
    return await json<Overview>("/api/overview", { next: { revalidate: 3600 } });
  } catch {
    return null;
  }
}
export const search = (q: string, signal?: AbortSignal) =>
  json<SearchResult>(`/api/search?q=${encodeURIComponent(q)}`, { signal });

export async function* ask(question: string, history: Turn[], signal?: AbortSignal): AsyncGenerator<AskEvent> {
  const response = await fetch(API_BASE + "/api/ask", { ...post({ question, history }), signal });
  if (response.status === 429) {
    yield { kind: "limited", retryAfter: Number(response.headers.get("retry-after")) || 60 };
    return;
  }
  if (!response.ok || !response.body) {
    yield { kind: "error", message: `요청이 실패했습니다(${response.status}).` };
    return;
  }
  for await (const { event, data } of readEvents(response.body)) {
    const d = data as Record<string, unknown>;
    if (event === "partial") yield { kind: "partial", answer: String(d.answer ?? "") };
    else if (event === "final") {
      yield { kind: "final", answer: d as unknown as Answer };
      return;
    } else if (event === "abstain") {
      yield { kind: "abstain", abstain: d as unknown as Abstain };
      return;
    } else if (event === "error") {
      yield { kind: "error", message: String(d.message ?? "오류") };
      return;
    }
  }
  // 끝 이벤트 없이 닫혔습니다. API 재시작이 진행 중인 답을 끊으면 이렇게 됩니다(ADR-107).
  // 그냥 끝내면 그 턴이 "답하는 중…"에 남아 입력창이 막힙니다(ADR-115).
  yield { kind: "error", message: CUT_OFF };
}

export const CUT_OFF = "답이 중간에 끊겼습니다. 다시 물어 주세요.";
