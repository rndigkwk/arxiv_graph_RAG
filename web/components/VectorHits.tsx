"use client";

import Link from "next/link";
import { useState } from "react";
import { type VectorHit, getPaper } from "@/lib/api";
import { MAX_SAVED } from "@/lib/paper";
import { useSaved } from "./useSaved";

// 넓은 경로에서 벡터 검색이 찾은 논문. 근거 표에는 제목만 있어서, 왜 찾혔는지(초록·유사도)는 여기서 봅니다.
export function VectorHits({ hits }: { hits: VectorHit[] }) {
  const { saved, toggle } = useSaved();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  // 벡터 검색 결과에는 저자와 게재일이 없습니다. 저장할 때 논문 API에서 읽어 BibTeX를 채웁니다 -
  // 안 채우면 `author`가 빈 항목이 되고, 서지 도구에서 쓸 수 없습니다(ADR-042).
  async function save(id: string) {
    const isSaved = saved.some((s) => s.arxiv_id === id);
    if (!isSaved && saved.length >= MAX_SAVED) {
      setMessage(`저장 목록은 최대 ${MAX_SAVED}편입니다.`);
      return;
    }
    setBusy(id);
    setMessage("");
    try {
      const paper = await getPaper(id);
      if (paper) toggle(paper);
      else setMessage("그래프에서 이 논문을 찾지 못해 저장하지 않았습니다.");
    } catch {
      setMessage("논문 정보를 읽지 못해 저장하지 않았습니다. 잠시 뒤 다시 시도해 주세요.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <details className="section">
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        벡터로 찾은 논문의 초록 <span className="mono sub">{hits.length}편</span>
      </summary>
      {message && <p className="note">{message}</p>}
      <ul className="plain-list" style={{ marginTop: 10 }}>
        {hits.map((hit) => {
          const isSaved = saved.some((s) => s.arxiv_id === hit.arxiv_id);
          return (
            <li key={hit.arxiv_id} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span>
                <Link href={`/papers/${hit.arxiv_id}`}>{hit.title ?? hit.arxiv_id}</Link>{" "}
                <span className="mono sub">
                  {hit.arxiv_id}
                  {hit.score != null && ` · 벡터 유사도 ${hit.score.toFixed(3)} (클수록 유사)`}
                  {hit.categories?.length ? ` · ${hit.categories.join(", ")}` : ""}
                </span>
              </span>
              {hit.abstract && <p style={{ margin: 0, color: "var(--muted)", fontSize: 14 }}>{hit.abstract}</p>}
              <div className="actions">
                <button className="btn" onClick={() => save(hit.arxiv_id)} disabled={busy === hit.arxiv_id} aria-pressed={isSaved}>
                  {busy === hit.arxiv_id ? "읽는 중…" : isSaved ? "저장됨" : "저장"}
                </button>
                {hit.pdf_url && (
                  <a className="btn btn-link" href={hit.pdf_url} target="_blank" rel="noreferrer">
                    arXiv PDF
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
