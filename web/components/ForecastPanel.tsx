"use client";

import Link from "next/link";
import { useState } from "react";
import { type Forecast, getForecast } from "@/lib/api";

// 앞으로 함께 인용될 논문(ADR-038). 관련 논문과 **다른 질문**이라 따로 두고, 접은 채로 둡니다.
// 버튼을 눌러야 돕니다 - 질문마다 돌리면 보지 않는 사람도 기다립니다.
export function ForecastPanel({ id }: { id: string }) {
  const [result, setResult] = useState<Forecast | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function load() {
    setBusy(true);
    setFailed(false);
    try {
      setResult(await getForecast(id));
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="section">
      <summary style={{ cursor: "pointer", fontWeight: 500 }}>앞으로 함께 인용될 논문 (예측 모델)</summary>
      <p className="route">
        지금 함께 인용되는 논문이 아니라, <b>앞으로</b> 이 논문과 함께 인용될 논문을 자체 학습한 모델로 예측합니다.
        정답은 아직 오지 않았고, 모델이 통과한 기준은 과거 구간에서 잰 것입니다.
      </p>
      {!result && (
        <div className="actions">
          <button className="btn" disabled={busy} onClick={load}>
            {busy ? "후보를 모아 점수를 냅니다…" : "예측 보기"}
          </button>
        </div>
      )}
      {failed && <p className="note">예측을 읽지 못했습니다. 잠시 뒤 다시 눌러 주세요.</p>}
      {/* 왜 비었는지 적습니다. 빈 패널만 보이면 고장과 구별되지 않습니다. */}
      {result?.reason && <p className="note">{result.reason}</p>}
      {result && !result.reason && (
        <>
          <ol className="plain-list numbered">
            {result.papers.map((p) => (
              <li key={p.arxiv_id}>
                <Link href={`/papers/${p.arxiv_id}`}>{p.title || p.arxiv_id}</Link>
                <span className="mono sub">
                  점수 {p.score.toFixed(3)} · 지금 공동인용 {p.shared_cocitations} · 초록 유사 {p.embedding_cosine.toFixed(2)} ·{" "}
                  {p.arxiv_id}
                </span>
              </li>
            ))}
          </ol>
          <p className="route mono">
            후보 {result.candidates ?? 0}편에서 재정렬 · 모델 지문 {result.model?.digest ?? "—"} · 스냅샷{" "}
            {result.model?.snapshot_version ?? "—"} · 학습 과제 MRR {result.model?.ranking_mrr ?? "—"}
          </p>
        </>
      )}
    </details>
  );
}
