"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { type CitingOrder, type CitingPaper, getCiting } from "@/lib/api";
import { year } from "@/lib/paper";

const PAGE = 20;
const ORDERS: { key: CitingOrder; label: string }[] = [
  { key: "recent", label: "최근 게재순" },
  { key: "cited", label: "많이 인용된 순" },
];

export function CitingList({ id, total }: { id: string; total: number }) {
  const [order, setOrder] = useState<CitingOrder>("recent");
  const [papers, setPapers] = useState<CitingPaper[]>([]);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function load(nextOrder: CitingOrder, skip: number) {
    setBusy(true);
    setFailed(false);
    try {
      const page = await getCiting(id, nextOrder, skip, PAGE);
      setPapers((old) => (skip === 0 ? page.papers : [...old, ...page.papers]));
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (total > 0) load(order, 0);
  }, [id, order, total]);

  return (
    <section className="card panel">
      <h2>
        이 논문을 인용한 논문 <span className="mono sub">{total.toLocaleString("ko-KR")}</span>
      </h2>
      {total === 0 ? (
        <p className="route">이 그래프 안에서 이 논문을 인용한 논문은 없습니다.</p>
      ) : (
        <>
          <div className="actions" role="group" aria-label="정렬">
            {ORDERS.map((o) => (
              <button key={o.key} className="btn" aria-pressed={order === o.key} onClick={() => setOrder(o.key)}>
                {o.label}
              </button>
            ))}
          </div>
          <ul className="plain-list">
            {papers.map((p) => (
              <li key={p.arxiv_id}>
                <Link href={`/papers/${p.arxiv_id}`}>{p.title}</Link>
                <span className="mono sub">
                  {p.arxiv_id} · {p.primary_category} · {year(p.published)}
                  {p.cited_by > 0 ? ` · 인용 ${p.cited_by}` : ""}
                </span>
              </li>
            ))}
          </ul>
          {failed && <p className="note">목록을 읽지 못했습니다. 잠시 뒤 다시 눌러 주세요.</p>}
          {papers.length < total && (
            <div className="actions">
              <button className="btn" disabled={busy} onClick={() => load(order, papers.length)}>
                {busy ? "읽는 중…" : `다음 ${Math.min(PAGE, total - papers.length)}편`}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
