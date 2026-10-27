"use client";

import Link from "next/link";
import { useState } from "react";
import type { Paper } from "@/lib/api";
import { askHref, questionsFor } from "@/lib/paper";
import { useSaved } from "./useSaved";

export function PaperActions({ paper }: { paper: Pick<Paper, "arxiv_id" | "title" | "primary_category" | "bibtex"> }) {
  const { saved, toggle } = useSaved();
  const [showBib, setShowBib] = useState(false);
  const [copied, setCopied] = useState(false);
  const isSaved = saved.some((s) => s.arxiv_id === paper.arxiv_id);

  async function copy() {
    try {
      await navigator.clipboard.writeText(paper.bibtex);
      setCopied(true);
    } catch {
      setShowBib(true); // 복사가 막히면 보여 주고 직접 고르게 합니다.
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="actions">
        <Link className="btn btn-primary btn-link" href={askHref(questionsFor(paper)[0].question)}>
          이 논문에 대해 묻기
        </Link>
        <button className="btn" onClick={() => toggle(paper)} aria-pressed={isSaved}>
          {isSaved ? "저장됨" : "저장"}
        </button>
        <button className="btn" onClick={() => setShowBib((o) => !o)} aria-expanded={showBib}>
          BibTeX
        </button>
      </div>
      {showBib && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <pre className="cypher mono">{paper.bibtex}</pre>
          <div className="actions">
            <button className="btn" onClick={copy}>
              {copied ? "복사했습니다" : "복사"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
