// 탐색 화면의 순수 부분: 조건을 질의 문자열로, 순위를 CSV로.

import type { RankBy, RankQuery, RankedPaper } from "./api";

export const PAGE = 20;

export const BY_LABEL: Record<RankBy, string> = {
  cited: "많이 인용된 순",
  pagerank: "영향력 순 (PageRank)",
  personalized: "한 논문과 가까운 순",
};

// 기준마다 한 줄 설명. 이름만으로는 PageRank가 무엇을 재는지 알 수 없습니다(ADR-109).
export const BY_HINT: Record<RankBy, string> = {
  cited: "이 그래프 안에서 인용된 횟수입니다.",
  pagerank: "많이 인용된 논문에게 인용받을수록 높아집니다. 인용 수가 적어도 위로 갈 수 있습니다.",
  personalized: "고른 논문에서 인용을 따라 가까운 논문입니다. 인용 방향은 따지지 않습니다.",
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

// 피인용 수는 정수로 보여 줍니다. PageRank 점수(6.00e-4 같은 값)는 그대로 읽히지 않아 1위를 100으로 둔
// 상대 점수로 보여 줍니다(ADR-109). 원래 점수는 CSV에 남습니다. 막대도 1위에 맞춘 상대 크기입니다.
export const valueOf = (by: RankBy, p: RankedPaper) => (by === "cited" ? p.cited_by : p.score);
export const barShare = (value: number, top: number) => (top > 0 ? Math.max(0.01, value / top) : 0);

export function formatValue(by: RankBy, value: number, top = value): string {
  if (by === "cited") return value.toLocaleString("ko-KR");
  return top > 0 ? String(Math.round((value / top) * 100)) : "0";
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
