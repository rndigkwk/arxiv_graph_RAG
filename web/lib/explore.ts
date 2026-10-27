// 탐색 화면의 순수 부분: 조건을 질의 문자열로, 순위를 CSV로.

import type { RankBy, RankQuery, RankedPaper } from "./api";

export const PAGE = 20;

export const BY_LABEL: Record<RankBy, string> = {
  cited: "그래프 안 피인용 수",
  pagerank: "PageRank",
  personalized: "한 논문에서 출발(개인화 PageRank)",
};

export function rankQuery(q: RankQuery): string {
  const params = new URLSearchParams({ by: q.by, skip: String(q.skip), limit: String(PAGE) });
  // 개인화는 출발 논문이 정하므로 분류 범위를 보내지 않습니다. 분류 안 인용만으로 계산하는 것은 PageRank뿐입니다.
  if (q.category) {
    params.set("category", q.category);
    params.set("include_secondary", String(q.includeSecondary));
    if (q.by === "pagerank") params.set("scope", q.scope);
  }
  if (q.by === "personalized" && q.seed) params.set("seed", q.seed);
  return params.toString();
}

// 피인용 수는 정수로, PageRank는 점수로 보여 줍니다. 막대는 이 목록의 1위에 맞춘 상대 크기입니다.
export const valueOf = (by: RankBy, p: RankedPaper) => (by === "cited" ? p.cited_by : p.score);
export const barShare = (value: number, top: number) => (top > 0 ? Math.max(0.01, value / top) : 0);

export function formatValue(by: RankBy, value: number): string {
  return by === "cited" ? value.toLocaleString("ko-KR") : value.toExponential(2);
}

const cell = (v: unknown) => {
  const text = v == null ? "" : String(v);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

export function rankingCsv(by: RankBy, papers: RankedPaper[]): string {
  const head = ["rank", "arxiv_id", "title", "primary_category", "published", "cited_by_in_graph", by === "cited" ? "" : "score"].filter(Boolean);
  const rows = papers.map((p) =>
    [p.rank, p.arxiv_id, p.title, p.primary_category, p.published?.slice(0, 10), p.cited_by, ...(by === "cited" ? [] : [p.score])].map(cell).join(","),
  );
  return [head.join(","), ...rows].join("\n") + "\n";
}
