// 품질 화면. 숫자는 API가 커밋된 기준선에서 읽은 그대로이고, 여기서는 모양만 바꿉니다.

import { API_BASE } from "./api";

export type Gate = {
  metric: string;
  measured: number | null;
  floor: number | null;
  allowed_drop: number | null;
  headroom: number | null;
  observed_spread: number | null;
  gated: boolean;
};
export type Family = {
  family: string;
  cases: number;
  runs: number;
  failure_rate: number;
  gated_context_precision: boolean;
  hit_at_k: number | null;
  answer_evidence_recall: number | null;
  [key: string]: unknown;
};
export type Quality = {
  summary: {
    generated_at: string;
    runs: number;
    dataset: string;
    cases: number;
    stable_cases: number;
    gated_metrics: number;
    judge_model: string;
    tight_floors: string[];
    prompts_drifted: string[];
  };
  rules: { name: string; blocks: string }[];
  gates: Gate[];
  families: Family[] | null;
  // 아래 셋은 API가 나중에 더한 칸입니다. VM 배포보다 Vercel 빌드가 먼저 끝나면 비어 옵니다.
  rationale?: Record<string, string>;
  slots?: { label: string; metric: string; variant: string | null; floor: number | null; measured: number | null; checked_by: string }[];
  rejected?: { model: string; slot: string; result: string; record: string }[];
  basis?: Basis[];
  prompts?: { prompt: string; baseline: string | null; code: string | null; same: boolean | null }[];
  future?: Future | null;
};
// RAGAS 하한에 결함을 심어 걸리는지 본 표(`quality_board.basis_rows`).
export type Basis = {
  metric: string;
  floor: number;
  runs_seen: number;
  worst_normal_run: number;
  margin: number;
  survives: boolean;
  with_defect: number[];
  defect_trips_it: boolean;
};
// 관련 논문 변형 비교(미래 라벨 과제)와 연결 정도별 recall@5.
export type Future = {
  cases: number;
  feature_cut: string;
  variants: {
    variant: string;
    deployed: boolean;
    recall_at_k: number | null;
    hit_at_k: number | null;
    reciprocal_rank: number | null;
    vs_incumbent: string;
  }[];
  degree_variants: string[];
  degrees: ({ group: string; seeds: number; share: number } & Record<string, number | string>)[];
};

export const yesNo = (v: boolean | null | undefined) => (v == null ? "—" : v ? "예" : "아니오");

// 기준선은 PR로만 바뀌므로 한 시간 캐시합니다.
export async function getQuality(): Promise<Quality | null> {
  try {
    const response = await fetch(API_BASE + "/api/quality", { next: { revalidate: 3600 } });
    return response.ok ? ((await response.json()) as Quality) : null;
  } catch {
    return null;
  }
}

export const lowerIsBetter = (metric: string) => metric.endsWith("_failure_rate") || metric.endsWith("_errors");

export function floorText(gate: Gate): string {
  if (gate.floor == null) return "—";
  return lowerIsBetter(gate.metric) ? `≤ ${gate.floor}` : String(gate.floor);
}

export const num = (v: number | null | undefined, digits = 3) => (v == null ? "—" : Number(v.toFixed(digits)).toString());

// 판정 지표는 세 실행의 평균과 그 폭입니다. 폭이 없으면 평균만 적습니다(`app.py`의 표와 같은 모양).
export function judged(row: Family, name: string): string {
  const value = row[name] as number | null | undefined;
  const range = row[`${name}_range`] as [number, number] | null | undefined;
  if (value == null) return "—";
  if (!range || range[0] === range[1]) return value.toFixed(3);
  return `${value.toFixed(3)} (${range[0].toFixed(2)}~${range[1].toFixed(2)})`;
}

// 검색·근거 지표가 모든 분야에서 1.0이고 실패가 없으면, 이 문항으로는 분야를 가르지 못합니다.
export const atCeiling = (rows: Family[]) =>
  rows.every((r) => r.hit_at_k === 1 && r.answer_evidence_recall === 1 && r.failure_rate === 0);

export const FAMILY_LABEL: Record<string, string> = {
  author: "저자",
  citing: "인용한 논문",
  coauthor: "공저자",
  reference: "참고문헌",
  topcited: "최다 피인용",
};
