// VM의 FastAPI 층(ADR-081)을 부릅니다. 브라우저가 VM을 직접 부릅니다 - Vercel을 거치면 VM이 보는
// IP가 Vercel 것이 되어 IP별 상한이 한 덩어리가 됩니다.

import { readEvents } from "./sse";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "https://8-230-25-138.sslip.io").replace(/\/$/, "");

export type Route = { name: "graph" | "any"; cues: string[]; vetoes: string[] };
export type ToolCall = { id: string; name: string; args: unknown; status?: string };
export type Related = { arxiv_id: string; title?: string; score?: number; reason?: string };
export type Answer = {
  question: string;
  answer: string;
  evidence_ids: string[];
  tool_calls: ToolCall[];
  cyphers: string[];
  rows: Record<string, unknown>[];
  papers: { arxiv_id: string; title?: string }[];
  related: Related[];
};
export type Abstain = { message: string; stage?: string; reason?: string; attempts?: number };
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
export type Stats = { counts: { papers: number; references: number }; top_categories: { code: string; papers: number }[] };

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
    else if (event === "final") yield { kind: "final", answer: d as unknown as Answer };
    else if (event === "abstain") yield { kind: "abstain", abstain: d as unknown as Abstain };
    else if (event === "error") yield { kind: "error", message: String(d.message ?? "오류") };
  }
}
