"use client";

import Link from "next/link";
import { MAX_SAVED, bibtexFile, savedCsv, savedGaps } from "@/lib/paper";
import { useSaved } from "./useSaved";

export function SavedList() {
  const { saved, toggle } = useSaved();

  function download(text: string, type: string, name: string) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  const gaps = savedGaps(saved);
  const gapText = [gaps.author && `저자가 빈 항목 ${gaps.author}건`, gaps.year && `게재 연도가 빈 항목 ${gaps.year}건`].filter(Boolean).join(" · ");

  return (
    <section className="card panel">
      <h2>
        저장한 논문{" "}
        <span className="mono sub">
          {saved.length} / {MAX_SAVED}
        </span>
      </h2>
      {saved.length === 0 ? (
        <p className="route">논문 페이지의 "저장"을 누르면 여기 모입니다. 이 브라우저에만 남습니다.</p>
      ) : (
        <>
          <ul className="plain-list">
            {saved.map((s) => (
              <li key={s.arxiv_id} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                <span style={{ flex: 1 }}>
                  <Link href={`/papers/${s.arxiv_id}`}>{s.title}</Link>
                  <span className="mono sub">{s.arxiv_id}</span>
                </span>
                <button className="btn" onClick={() => toggle(s)} aria-label={`${s.title} 저장 취소`}>
                  빼기
                </button>
              </li>
            ))}
          </ul>
          <div className="actions">
            <button className="btn" onClick={() => download(bibtexFile(saved), "application/x-bibtex", "arxiv-graphrag-saved.bib")}>
              BibTeX로 받기
            </button>
            <button className="btn" onClick={() => download(savedCsv(saved), "text/csv", "arxiv-graphrag-saved.csv")}>
              CSV로 받기
            </button>
          </div>
          {/* 빈 칸이 보이지 않으면 불완전한 파일을 완전한 줄 알고 인용합니다(ADR-042). */}
          {gapText && <p className="route">{gapText} — 그래프에서 읽지 못한 값은 추측하지 않고 비워 둡니다.</p>}
        </>
      )}
    </section>
  );
}
