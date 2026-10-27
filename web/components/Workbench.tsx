"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { type Abstain, type Answer, type Route, type Subgraph, ask, getRoute, getSubgraph, translate } from "@/lib/api";
import { attemptsOf, citedEvidence, graphIds, historyFor, routeLine, seconds, stageRows, usedPath } from "@/lib/answer";
import { EvidenceGraph } from "./EvidenceGraph";
import { VectorHits } from "./VectorHits";
import { Markdown } from "./Markdown";

type Status = "streaming" | "done" | "abstain" | "error" | "limited";
type Turn = {
  id: number;
  question: string;
  status: Status;
  partial: string;
  ms?: number;
  answer?: Answer;
  abstain?: Abstain;
  message?: string;
  subgraph?: Subgraph;
  korean?: string;
};

const EXAMPLE = "Which papers cite ReAct: Synergizing Reasoning and Acting in Language Models?";
// Streamlit 사이드바의 예시와 같습니다. 무엇을 물을 수 있는지(저자·피인용·인용·개인화 PageRank) 보여 줍니다.
const EXAMPLES = [
  "지식 그래프 추론에 그래프 신경망을 쓰는 논문을 찾아 줘",
  "cs.AI 논문 중에서 피인용이 가장 많은 논문 5편은?",
  "Yoshua Bengio가 쓴 논문이 있어?",
  "대규모 언어 모델 에이전트를 다루는 cs.CL 논문을 알려 줘",
  "Attention Is All You Need를 인용한 논문을 알려 줘",
  "2026년에 나온 cs.AI 논문 중 피인용이 많은 3편은?",
  "2303.11366 논문과 관련된 논문을 알려 줘",
];

// `initial`은 논문 페이지에서 넘어온 질문입니다. 칸만 채우고 보내지는 않습니다(보내면 돈이 듭니다).
export function Workbench({ initial = "" }: { initial?: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [draft, setDraft] = useState(initial);
  const [route, setRoute] = useState<Route | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [translating, setTranslating] = useState(false);
  const nextId = useRef(1);
  const input = useRef<HTMLInputElement>(null);
  const busy = turns.some((t) => t.status === "streaming");

  // 보내기 전에 경로를 판정합니다(모델 없이, 0원). 타자 칠 때마다 부르지 않고 잠깐 멈춘 뒤에.
  useEffect(() => {
    const q = draft.trim();
    if (q.length < 4) {
      setRoute(null);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      getRoute(q, controller.signal).then(setRoute).catch(() => {});
    }, 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [draft]);

  const update = (id: number, patch: Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  async function submit(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const id = nextId.current++;
    const history = historyFor(
      turns.filter((t) => t.status === "done" && t.answer).map((t) => ({ question: t.question, answer: t.answer!.answer })),
    );
    setTurns((all) => [...all, { id, question: q, status: "streaming", partial: "" }]);
    setCurrent(id);
    setDraft("");
    setTraceOpen(false);
    const started = performance.now();
    try {
      for await (const event of ask(q, history)) {
        const ms = performance.now() - started;
        if (event.kind === "partial") update(id, { partial: event.answer });
        else if (event.kind === "final") {
          update(id, { status: "done", answer: event.answer, ms });
          const ids = graphIds(event.answer);
          if (ids.length) getSubgraph(ids).then((subgraph) => update(id, { subgraph })).catch(() => {});
        } else if (event.kind === "abstain") update(id, { status: "abstain", abstain: event.abstain, ms });
        else if (event.kind === "limited")
          update(id, { status: "limited", message: `질문이 너무 많습니다. ${event.retryAfter}초 뒤 다시 물어 주세요.` });
        else update(id, { status: "error", message: event.message, ms });
      }
    } catch {
      update(id, { status: "error", message: "API에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요." });
    }
  }

  async function toKorean(turn: Turn) {
    if (!turn.answer || translating) return;
    setTranslating(true);
    try {
      update(turn.id, { korean: (await translate(turn.answer.answer)).text });
    } catch {
      update(turn.id, { korean: "번역하지 못했습니다(요청 상한이거나 연결 문제입니다)." });
    } finally {
      setTranslating(false);
    }
  }

  const turn = turns.find((t) => t.id === current) ?? null;

  return (
    <div className="bench">
      <aside aria-label="대화" className="bench-side">
        <button
          className="btn"
          style={{ height: 44, borderColor: "var(--accent)", color: "var(--accent)", fontWeight: 600 }}
          onClick={() => {
            setTurns([]);
            setCurrent(null);
          }}
          disabled={busy}
        >
          + 새 대화
        </button>
        {turns.length > 0 && (
          <section style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <p className="lbl">이 대화</p>
            {[...turns].reverse().map((t) => (
              <button key={t.id} className="turn-link" aria-current={t.id === current} onClick={() => setCurrent(t.id)}>
                <span className="mono">{label(t)}</span>
                {t.question}
              </button>
            ))}
          </section>
        )}
      </aside>

      <div className="bench-main">
        <form
          className="card ask-form"
          onSubmit={(e) => {
            e.preventDefault();
            submit(draft);
          }}
        >
          <label htmlFor="ask" className="lbl">
            질문
          </label>
          <div className="ask-row">
            <input
              id="ask"
              ref={input}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={EXAMPLE}
              maxLength={2000}
              autoComplete="off"
            />
            <button type="submit" className="btn btn-primary" disabled={busy || !draft.trim()}>
              {busy ? "답하는 중…" : "질문하기"}
            </button>
          </div>
          <p className="route">
            {route ? (
              <>
                입력 중인 질문은 <span className="mono">{routeLine(route)}</span> · 보내기 전에 판정합니다(모델 없이, 0원).
              </>
            ) : (
              "질문을 적으면 보내기 전에 어느 경로로 갈지 먼저 보여 줍니다(모델 없이, 0원)."
            )}
          </p>
        </form>

        {turn ? (
          <TurnView
            turn={turn}
            traceOpen={traceOpen}
            onToggleTrace={() => setTraceOpen((o) => !o)}
            onKorean={() => toKorean(turn)}
            translating={translating}
          />
        ) : (
          <div className="card empty">
            <p style={{ margin: "0 0 10px" }}>영어나 한국어로 물어보세요. 예시를 누르면 질문 칸에 채웁니다(보내기 전까지 돈이 들지 않습니다).</p>
            <ul className="plain-list">
              {EXAMPLES.map((q) => (
                <li key={q}>
                  <button
                    className="turn-link"
                    onClick={() => {
                      setDraft(q);
                      input.current?.focus();
                    }}
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
            <p className="route" style={{ margin: "10px 0 0" }}>답은 영어로 나오고, 답 아래 &quot;한국어로 보기&quot;로 번역합니다.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function label(t: Turn): string {
  if (t.status === "streaming") return "답하는 중…";
  if (t.status === "abstain") return "보류";
  if (t.status === "error" || t.status === "limited") return "실패";
  return [t.answer ? usedPath(t.answer) : "", t.ms ? seconds(t.ms) : ""].filter(Boolean).join(" · ");
}

function TurnView({
  turn,
  traceOpen,
  onToggleTrace,
  onKorean,
  translating,
}: {
  turn: Turn;
  traceOpen: boolean;
  onToggleTrace: () => void;
  onKorean: () => void;
  translating: boolean;
}) {
  const a = turn.answer;
  const titles = new Map((turn.subgraph?.papers ?? []).map((p) => [p.arxiv_id, p]));
  const evidence = citedEvidence(a ?? ({ evidence_ids: [], rows: [], papers: [] } as unknown as Answer));
  const stages = a ? stageRows(a) : [];
  const meta = a
    ? [
        usedPath(a),
        turn.ms ? seconds(turn.ms) : "",
        `근거 ${a.evidence_ids.length}편`,
        // 재시도는 느린 답의 흔한 이유입니다. 숨기면 합계만 보고 모델을 의심하게 됩니다.
        attemptsOf(a) > 1 ? `시도 ${attemptsOf(a)}회` : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : turn.status === "streaming"
      ? "답을 만드는 중…"
      : "";

  return (
    <div className="answer-row">
      <article className="card answer" aria-live="polite" aria-busy={turn.status === "streaming"}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h1>{turn.question}</h1>
          {meta && <p className="meta mono">{meta}</p>}
        </div>

        {turn.status === "abstain" && turn.abstain && <AbstainView abstain={turn.abstain} />}
        {(turn.status === "error" || turn.status === "limited") && <p className="note">{turn.message}</p>}

        {(a?.answer ?? turn.partial) && <Markdown text={a?.answer ?? turn.partial} />}
        {turn.korean && <Markdown text={turn.korean} muted />}

        {a && a.evidence_ids.length > 0 && (
          <section className="section" style={{ overflowX: "auto" }}>
            <h2>
              근거로 쓴 논문 <span style={{ fontWeight: 400, color: "var(--muted)" }}>— 제목은 답이 아니라 그래프에서 읽습니다</span>
            </h2>
            <table>
              <thead>
                <tr>
                  <th>arXiv ID</th>
                  <th>제목</th>
                  <th>분류</th>
                </tr>
              </thead>
              <tbody>
                {a.evidence_ids.map((id) => (
                  <tr key={id}>
                    <td className="mono" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
                      <Link href={`/papers/${id}`}>{id}</Link>
                    </td>
                    <td>{titles.get(id)?.title ?? "…"}</td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {titles.get(id)?.primary_category ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {a && (
          <div className="actions">
            <button className="btn" onClick={onKorean} disabled={translating || !!turn.korean}>
              {translating ? "번역 중…" : "한국어로 보기"}
            </button>
            <button className="btn trace-toggle" onClick={onToggleTrace} aria-expanded={traceOpen}>
              {traceOpen ? "근거 닫기" : "근거 보기"}
            </button>
          </div>
        )}

        {a && a.related.length > 0 && (
          <section className="section">
            <h2>
              이어서 읽을 논문 <span style={{ fontWeight: 400, color: "var(--muted)" }}>— 근거와 함께 인용된 순서</span>
            </h2>
            <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 }}>
              {a.related.map((r) => (
                <li key={r.arxiv_id}>
                  <Link href={`/papers/${r.arxiv_id}`}>{r.title ?? titles.get(r.arxiv_id)?.title ?? r.arxiv_id}</Link>{" "}
                  <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
                    {r.arxiv_id}
                  </span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {a && a.papers.length > 0 && <VectorHits hits={a.papers} />}

        {a && turn.subgraph && (
          <section className="section">
            <h2>그래프</h2>
            <EvidenceGraph subgraph={turn.subgraph} evidence={a.evidence_ids} />
          </section>
        )}
      </article>

      {a && (
        <aside aria-label="이 답변의 경로와 근거" className="trace" data-open={traceOpen}>
          <section className="card">
            <p className="lbl">실행한 Cypher</p>
            {a.cyphers.length ? (
              a.cyphers.map((c, i) => (
                <pre key={i} className="cypher mono">
                  {c}
                </pre>
              ))
            ) : (
              <p style={{ margin: 0, color: "var(--muted)" }}>그래프 질의 없이 의미 검색으로 답했습니다.</p>
            )}
            <dl className="facts">
              <dt>돌아온 행</dt>
              <dd className="mono">{a.rows.length}</dd>
              <dt>조회 결과에 있던 근거</dt>
              <dd className="mono">
                {evidence.found} / {evidence.total}
              </dd>
              <dt>걸린 시간</dt>
              <dd className="mono">{turn.ms ? seconds(turn.ms) : "—"}</dd>
            </dl>
          </section>
          {stages.length > 0 && (
            <section className="card">
              <p className="lbl">단계별 시간</p>
              <table className="mono" style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>단계</th>
                    <th>초</th>
                    <th>방문</th>
                  </tr>
                </thead>
                <tbody>
                  {stages.map((r) => (
                    <tr key={r.node} style={r.node === "합계" ? { fontWeight: 600 } : undefined}>
                      <td>
                        {r.node}
                        {r.note && <span className="sub">{r.note}</span>}
                      </td>
                      <td>{r.seconds.toFixed(2)}</td>
                      <td>{r.visits}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="route" style={{ margin: "8px 0 0" }}>
                서버가 단계마다 잰 시간입니다. 위의 &quot;걸린 시간&quot;은 브라우저가 잰 것이라 네트워크만큼 더 깁니다.
              </p>
            </section>
          )}
          <details className="card" style={{ padding: "14px 16px" }}>
            <summary style={{ cursor: "pointer", fontWeight: 500, minHeight: 24 }}>도구를 부른 순서</summary>
            <ol className="mono" style={{ margin: "10px 0 0", paddingLeft: 20, fontSize: 12 }}>
              {a.tool_calls.map((c) => (
                <li key={c.id}>
                  {c.name}
                  {c.status && c.status !== "success" ? ` (${c.status})` : ""}
                </li>
              ))}
            </ol>
          </details>
        </aside>
      )}
    </div>
  );
}

// 보류는 고장이 아니라 판단입니다(`src/abstain.py`). 문장은 API가 붙여 보내고, 여기서는 그리기만 합니다.
function AbstainView({ abstain }: { abstain: Abstain }) {
  const told = abstain.explained;
  if (!told) {
    // VM 배포보다 Vercel 빌드가 먼저 끝나면 문장 없이 옵니다.
    return (
      <p className="note">
        <b>답을 보류했습니다.</b> {abstain.message}
        {abstain.stage && <span className="mono"> (단계 {abstain.stage})</span>} — 질문을 더 구체적으로 고쳐 물어 보세요.
      </p>
    );
  }
  return (
    <div className="note" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <p style={{ margin: 0 }}>
        <b>{told.headline}</b> {told.why}
      </p>
      <p style={{ margin: 0 }}>
        <b>무엇이 걸렸나</b> — {told.what}
      </p>
      <p style={{ margin: 0 }}>
        <b>해 볼 것</b> — {told.advice}
      </p>
      <details>
        <summary style={{ cursor: "pointer", fontWeight: 500 }}>검사 기록</summary>
        <dl className="facts" style={{ marginTop: 8 }}>
          <dt>걸린 단계</dt>
          <dd>{told.stage || "기록되지 않음"}</dd>
          <dt>시도 횟수</dt>
          <dd className="mono">{told.attempts}</dd>
          <dt>요청 ID</dt>
          <dd className="mono">{told.request_id || "없음"}</dd>
        </dl>
        {/* 모델이 실제로 무엇을 조회했는지. 멈춘 것이 "아무것도 안 한 것"이 아님을 이것이 말해 줍니다. */}
        {told.cyphers.length ? (
          <>
            <p className="route">모델이 실행한 조회 {told.cyphers.length}건</p>
            {told.cyphers.map((c, i) => (
              <pre key={i} className="cypher mono">
                {c}
              </pre>
            ))}
          </>
        ) : (
          <p className="route">실행된 그래프 조회가 없습니다.</p>
        )}
      </details>
    </div>
  );
}
