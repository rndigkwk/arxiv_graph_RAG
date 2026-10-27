// 그래프 소개 화면의 그림 계산. 그림 라이브러리 없이 SVG로 그리려고 좌표만 여기서 셉니다.

import type { Community, Overview } from "./api";

// 개수 차이가 커서 넓이가 아니라 제곱근에 비례하게 키웁니다(`src/metagraph.py`와 같은 규칙).
export function sqrtScale(value: number, largest: number, smallest: number, biggest: number): number {
  if (!largest) return smallest;
  return smallest + (biggest - smallest) * Math.sqrt(Math.max(0, value) / largest);
}

export type Placed = Community & { x: number; y: number; r: number };

/** 커뮤니티를 원 위에 둡니다. 가장 큰 것이 위쪽 가운데이고 시계 방향으로 작아집니다. 실행마다 같은 배치입니다. */
export function placeCommunities(rows: Community[], width: number, height: number): Placed[] {
  const largest = Math.max(0, ...rows.map((r) => r.papers));
  const cx = width / 2;
  const cy = height / 2;
  const ring = Math.min(width, height) / 2 - 56;
  return rows.map((row, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / rows.length;
    return { ...row, x: cx + ring * Math.cos(angle), y: cy + ring * Math.sin(angle), r: sqrtScale(row.papers, largest, 18, 46) };
  });
}

export const PAPER_PROPERTIES: { name: string; type: string; note: string }[] = [
  { name: "arxiv_id", type: "STRING", note: "버전을 뗀 고유 ID (2303.11366)" },
  { name: "title", type: "STRING", note: "논문 제목 (영어)" },
  { name: "abstract", type: "STRING", note: "초록 (영어). 임베딩 입력의 일부" },
  { name: "published", type: "DATETIME", note: "게재일. 기간 필터에 씁니다" },
  { name: "pdf_url", type: "STRING", note: "arXiv PDF 링크" },
  { name: "primary_category", type: "STRING", note: "대표 분류 하나 (cs.AI). 인덱스 있음" },
  { name: "categories", type: "LIST<STRING>", note: "대표 분류를 포함한 모든 분류" },
  { name: "source", type: "STRING", note: "ai=수집한 논문, reference=인용으로 딸려온 논문" },
  { name: "embedding", type: "LIST<FLOAT>", note: "제목+초록 768차원 벡터" },
];

/** 선 굵기 범위의 기준. 자기 안의 인용은 선이 아니라 점 옆 숫자로 적습니다. */
export const biggestLink = (o: NonNullable<Overview["communities"]>) => Math.max(0, ...o.links.map((l) => l.citations));
