"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, type RankBy, type RankedPaper, type Stats, getRanking, getStats } from "@/lib/api";
import { BY_LABEL, PAGE, barShare, formatValue, rankQuery, rankingCsv, valueOf } from "@/lib/explore";
import { ARXIV_ID } from "@/lib/paper";

const CATEGORIES = 6;
const column = { border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 } as const;
const option = { display: "flex", gap: 8, minHeight: 28, alignItems: "center" } as const;

export function Explorer({ initialSeed = "" }: { initialSeed?: string }) {
  const [by, setBy] = useState<RankBy>(initialSeed ? "personalized" : "cited");
  const [category, setCategory] = useState("");
  const [includeSecondary, setIncludeSecondary] = useState(true);
  const [scope, setScope] = useState<"global" | "subgraph">("global");
  const [seed, setSeed] = useState(initialSeed);
  const [seedDraft, setSeedDraft] = useState(initialSeed);
  const [stats, setStats] = useState<Stats | null>(null);
  const [papers, setPapers] = useState<RankedPaper[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    getStats().then(setStats).catch(() => {});
  }, []);

  async function load(skip: number) {
    if (by === "personalized" && !seed) {
      setPapers([]);
      setTotal(0);
      setMessage("출발할 논문의 arXiv ID를 넣어 주세요.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const page = await getRanking(
        rankQuery({ by, category: category || undefined, includeSecondary, scope, seed, skip }),
      );
      setPapers((old) => (skip === 0 ? page.papers : [...old, ...page.papers]));
      setTotal(page.total);
      if (!page.papers.length && skip === 0)
        setMessage("이 조건에서는 인용으로 이어진 논문이 없어 순위를 만들 수 없습니다.");
    } catch (error) {
      if (skip === 0) setPapers([]);
      setMessage(
        error instanceof ApiError && error.status === 404
          ? "인용망에 없는 논문입니다. 인용 관계가 저장된 논문에서 출발하세요."
          : "순위를 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
      );
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    load(0);
  }, [by, category, includeSecondary, scope, seed]);

  function download() {
    const url = URL.createObjectURL(new Blob([rankingCsv(by, papers)], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `arxiv-graphrag-${by}${category ? "-" + category : ""}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const top = papers.length ? valueOf(by, papers[0]) : 0;
  const inGraph = stats ? stats.counts.papers.toLocaleString("ko-KR") : "…";
  const title =
    by === "personalized"
      ? `${seed || "한 논문"}에서 출발해 가까운 논문`
      : `${by === "cited" ? "가장 많이 인용된 논문" : "PageRank가 높은 논문"} — ${category || "전체 분류"}, 그래프 안 인용`;

  return (
    <div className="two-col">
      <aside className="card panel" style={{ flex: "1 1 260px", maxWidth: 360 }} aria-label="순위 조건">
        <fieldset style={column}>
          <legend className="lbl" style={{ marginBottom: 6 }}>
            순위 기준
          </legend>
          {(Object.keys(BY_LABEL) as RankBy[]).map((key) => (
            <label key={key} style={option}>
              <input type="radio" name="by" checked={by === key} onChange={() => setBy(key)} /> {BY_LABEL[key]}
            </label>
          ))}
        </fieldset>

        {by === "personalized" ? (
          <form
            className="ask-row"
            onSubmit={(e) => {
              e.preventDefault();
              const id = seedDraft.trim().split("v")[0];
              if (ARXIV_ID.test(id)) setSeed(id);
              else setMessage("arXiv ID 모양이 아닙니다(예: 2210.03629).");
            }}
          >
            <input
              aria-label="출발 논문 arXiv ID"
              value={seedDraft}
              onChange={(e) => setSeedDraft(e.target.value)}
              placeholder="2210.03629"
              style={{ flexBasis: 140, height: 40 }}
            />
            <button className="btn">출발</button>
          </form>
        ) : (
          <fieldset style={column}>
            <legend className="lbl" style={{ marginBottom: 6 }}>
              분류
            </legend>
            <div className="actions">
              <button className="btn" aria-pressed={!category} onClick={() => setCategory("")}>
                전체
              </button>
              {(stats?.top_categories ?? []).slice(0, CATEGORIES).map((c) => (
                <button
                  key={c.code}
                  className="btn mono"
                  aria-pressed={category === c.code}
                  onClick={() => setCategory(c.code)}
                >
                  {c.code}
                </button>
              ))}
            </div>
            {category && (
              <label style={option}>
                <input
                  type="checkbox"
                  checked={includeSecondary}
                  onChange={(e) => setIncludeSecondary(e.target.checked)}
                />{" "}
                보조 분류도 포함
              </label>
            )}
            {category && by === "pagerank" && (
              <label style={option}>
                <input
                  type="checkbox"
                  checked={scope === "subgraph"}
                  onChange={(e) => setScope(e.target.checked ? "subgraph" : "global")}
                />{" "}
                {category} 논문끼리의 인용만으로 계산
              </label>
            )}
          </fieldset>
        )}

        <p className="route">
          &quot;그래프 안&quot;은 이 앱에 적재된 {inGraph}편 사이의 인용만 셉니다. 바깥 세상의 피인용 수가 아닙니다.
          {by === "pagerank" && " PageRank는 중요한 논문에게 인용받을수록 올라가서, 피인용 수가 적어도 위로 갈 수 있습니다."}
          {by === "personalized" && " 출발 논문과 인용으로 가까운 논문입니다(인용 방향은 따지지 않습니다)."}
        </p>
      </aside>

      <section className="card panel" style={{ flex: "999 1 560px" }} aria-busy={busy}>
        <h1>{title}</h1>
        {total > 0 && <p className="meta mono">순위에 든 논문 {total.toLocaleString("ko-KR")}편</p>}
        {message && <p className="note">{message}</p>}
        {busy && !papers.length && <p className="route">순위를 계산하는 중…</p>}
        {papers.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>제목</th>
                  <th>분류</th>
                  <th>{by === "cited" ? "인용" : "점수"}</th>
                  <th style={{ width: "22%" }}>상대 크기</th>
                </tr>
              </thead>
              <tbody>
                {papers.map((p) => (
                  <tr key={p.arxiv_id}>
                    <td className="mono">{p.rank}</td>
                    <td>
                      <Link href={`/papers/${p.arxiv_id}`}>{p.title}</Link>
                      {by !== "cited" && <span className="mono sub">인용 {p.cited_by}</span>}
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {p.primary_category}
                    </td>
                    <td className="mono">{formatValue(by, valueOf(by, p))}</td>
                    <td>
                      <div className="bar" style={{ width: `${barShare(valueOf(by, p), top) * 100}%` }} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="actions">
          {papers.length > 0 && papers.length < total && (
            <button className="btn" disabled={busy} onClick={() => load(papers.length)}>
              {busy ? "읽는 중…" : `다음 ${Math.min(PAGE, total - papers.length)}편`}
            </button>
          )}
          {papers.length > 0 && (
            <button className="btn" onClick={download}>
              CSV로 받기
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
