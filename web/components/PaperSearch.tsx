"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { type SearchResult, search } from "@/lib/api";
import { ARXIV_ID } from "@/lib/paper";

// 제목이나 arXiv ID로 찾습니다. 전문 색인이라 모델을 부르지 않습니다(0원).
export function PaperSearch({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    const query = q.trim();
    if (ARXIV_ID.test(query)) {
      router.push(`/papers/${query.split("v")[0]}`);
      return;
    }
    if (query.length < 2) {
      setMessage("두 글자 이상 적어 주세요.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const found = await search(query);
      setResult(found);
      if (!found.candidates.length) setMessage("이 그래프에서 찾지 못했습니다. 제목을 더 길게 적어 보세요.");
    } catch {
      setMessage("API에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <form
        className="ask-row"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="paper-q" className="lbl" style={{ alignSelf: "center" }}>
          논문 찾기
        </label>
        <input
          id="paper-q"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="제목이나 arXiv ID"
          autoComplete="off"
          style={compact ? { flexBasis: 240 } : undefined}
        />
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "찾는 중…" : "찾기"}
        </button>
      </form>
      {message && <p className="route">{message}</p>}
      {result && result.candidates.length > 0 && (
        <ul className="card plain-list" aria-label="찾은 논문">
          {result.candidates.map((c) => (
            <li key={c.arxiv_id}>
              <Link href={`/papers/${c.arxiv_id}`}>{c.title}</Link>
              <span className="mono sub">
                {c.arxiv_id} · {c.primary_category}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
