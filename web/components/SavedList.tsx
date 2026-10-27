"use client";

import Link from "next/link";
import { MAX_SAVED, bibtexFile } from "@/lib/paper";
import { useSaved } from "./useSaved";

export function SavedList() {
  const { saved, toggle } = useSaved();

  function download() {
    const url = URL.createObjectURL(new Blob([bibtexFile(saved)], { type: "application/x-bibtex" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "arxiv-graphrag-saved.bib";
    a.click();
    URL.revokeObjectURL(url);
  }

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
            <button className="btn" onClick={download}>
              BibTeX로 받기
            </button>
          </div>
        </>
      )}
    </section>
  );
}
