"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { type Abstain, type Answer, type Subgraph, type SubgraphPaper, ask, getSubgraph, translateStream } from "@/lib/api";
import {
  EVIDENCE_PREVIEW,
  MAX_TRANSLATE_CHARS,
  attemptsOf,
  citedEvidence,
  forecastSeed,
  graphIds,
  hasHangul,
  historyFor,
  howLine,
  paperLine,
  rowCap,
  seconds,
  splitEvidence,
  stageRows,
} from "@/lib/answer";
import { EvidenceGraph } from "./EvidenceGraph";
import { ForecastPanel } from "./ForecastPanel";
import { VectorHits } from "./VectorHits";
import { Markdown } from "./Markdown";

type Status = "streaming" | "done" | "abstain" | "error" | "limited";
// 번역 상태. 한국어 질문은 답이 오자마자 번역을 부릅니다(ADR-108). 영어 질문은 누를 때만.
type Translation = "none" | "pending" | "done" | "failed" | "too-long";
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
  translation: Translation;
  lang: "ko" | "en";
};

const PLACEHOLDER = "예: Yoshua Bengio가 쓴 논문이 있어?";
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
  const nextId = useRef(1);
  const input = useRef<HTMLInputElement>(null);
  const busy = turns.some((t) => t.status === "streaming");

  const update = (id: number, patch: Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  async function toKorean(id: number, text: string) {
    if (text.length > MAX_TRANSLATE_CHARS) {
      update(id, { translation: "too-long" });
      return;
    }
    update(id, { translation: "pending" });
    // 번역을 흘려 받아 첫 조각부터 한국어로 보여 줍니다(ADR-114). 한국어로 넘기는 것은 첫 조각
    // 한 번뿐입니다 - 받는 동안 원문을 누른 사람을 다시 끌어오지 않습니다.
    let shown = false;
    try {
      const korean = await translateStream(text, (partial) => {
        if (!partial.trim()) return;
        update(id, shown ? { korean: partial } : { korean: partial, lang: "ko" });
        shown = true;
      });
      update(id, shown ? { korean, translation: "done" } : { korean, translation: "done", lang: "ko" });
    } catch {
      // 상한이거나 연결 문제입니다. 영어 답은 그대로 둡니다.
      // 반쯤 옮긴 글은 지웁니다. 끊긴 번역을 다 옮긴 글처럼 보여 주지 않습니다.
      update(id, { translation: "failed", korean: undefined, lang: "en" });
    }
  }

  async function submit(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const id = nextId.current++;
    const history = historyFor(
      turns.filter((t) => t.status === "done" && t.answer).map((t) => ({ question: t.question, answer: t.answer!.answer })),
    );
    setTurns((all) => [...all, { id, question: q, status: "streaming", partial: "", translation: "none", lang: "en" }]);
    setCurrent(id);
    setDraft("");
    const started = performance.now();
    try {
      for await (const event of ask(q, history)) {
        const ms = performance.now() - started;
        if (event.kind === "partial") update(id, { partial: event.answer });
        else if (event.kind === "final") {
          update(id, { status: "done", answer: event.answer, ms });
          const ids = graphIds(event.answer);
          if (ids.length) getSubgraph(ids).then((subgraph) => update(id, { subgraph })).catch(() => {});
          if (hasHangul(q)) toKorean(id, event.answer.answer);
        } else if (event.kind === "abstain") update(id, { status: "abstain", abstain: event.abstain, ms });
        else if (event.kind === "limited")
          update(id, { status: "limited", message: `질문이 너무 많습니다. ${event.retryAfter}초 뒤 다시 물어 주세요.` });
        else update(id, { status: "error", message: event.message, ms });
      }
    } catch {
      update(id, { status: "error", message: "API에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요." });
    }
  }

  const turn = turns.find((t) => t.id === current) ?? null;

  return (
    <div className="bench">
      <aside aria-label="최근 질문" className="bench-side">
        <button
          className="btn"
          style={{ borderColor: "var(--accent)", color: "var(--accent)", fontWeight: 600 }}
          onClick={() => {
            setTurns([]);
            setCurrent(null);
          }}
          disabled={busy}
        >
          + 새 질문
        </button>
        {turns.length > 0 && (
          <section style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <p className="lbl">최근 질문</p>
            {[...turns].reverse().map((t) => (
              <button key={t.id} className="turn-link" aria-current={t.id === current} onClick={() => setCurrent(t.id)}>
                {t.question}
                <span className="sub" data-warn={t.status !== "done" && t.status !== "streaming"}>
                  {label(t)}
                </span>
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
          <label htmlFor="ask" className="ask-label">
            무엇이 궁금하세요?
          </label>
          <div className="ask-row">
            <input
              id="ask"
              ref={input}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={PLACEHOLDER}
              maxLength={2000}
              autoComplete="off"
            />
            <button type="submit" className="btn btn-primary" disabled={busy || !draft.trim()}>
              {busy ? "답하는 중…" : "질문하기"}
            </button>
          </div>
          <p className="hint">한국어로 물어도 됩니다. arXiv 논문과 인용 관계에서만 답을 찾습니다.</p>
        </form>

        {turn ? (
          <TurnView key={turn.id} turn={turn} onKorean={() => turn.answer && toKorean(turn.id, turn.answer.answer)} onLang={(lang) => update(turn.id, { lang })} />
        ) : (
          <div className="card empty">
            <p style={{ margin: "0 0 10px" }}>이런 질문을 할 수 있습니다. 누르면 질문 칸에 채웁니다(보내기 전까지 돈이 들지 않습니다).</p>
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
          </div>
        )}
      </div>
    </div>
  );
}

function label(t: Turn): string {
  if (t.status === "streaming") return "답하는 중…";
  if (t.status === "abstain") return "답하지 않은 질문";
  if (t.status === "error" || t.status === "limited") return "답을 받지 못함";
  return [t.answer ? `근거 ${t.answer.evidence_ids.length}편` : "", t.ms ? seconds(t.ms) : ""].filter(Boolean).join(" · ");
}

function TurnView({ turn, onKorean, onLang }: { turn: Turn; onKorean: () => void; onLang: (lang: "ko" | "en") => void }) {
  const a = turn.answer;
  const [allEvidence, setAllEvidence] = useState(false);
  const titles = new Map((turn.subgraph?.papers ?? []).map((p) => [p.arxiv_id, p]));
  const showKorean = turn.lang === "ko" && !!turn.korean;
  const meta = a
    ? [`근거 논문 ${a.evidence_ids.length}편`, turn.ms ? seconds(turn.ms) : ""].filter(Boolean).join(" · ")
    : turn.status === "streaming"
      ? "답을 만드는 중…"
      : "";
  // 답의 표에 이미 있는 근거는 카드로 다시 그리지 않습니다(ADR-110). 표가 전부 덮으면 카드는 접어 둡니다.
  const { inTable, rest } = a ? splitEvidence(a) : { inTable: [], rest: [] };
  const evidence = allEvidence ? rest : rest.slice(0, EVIDENCE_PREVIEW);
  const cap = a ? rowCap(a) : null;
  const capNote = cap ? `그래프 조회가 ${cap}편에서 멈췄습니다. 더 있을 수 있어요 — 연도나 분야를 붙여 다시 물으면 좁혀집니다.` : "";

  return (
    <div className="stack">
      <article className="card answer" aria-live="polite" aria-busy={turn.status === "streaming"}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h1>{turn.question}</h1>
          {meta && <p className="meta">{meta}</p>}
        </div>

        {turn.status === "abstain" && turn.abstain && <AbstainView abstain={turn.abstain} />}
        {(turn.status === "error" || turn.status === "limited") && <p className="note">{turn.message}</p>}

        {a && (
          <div className="lang-row">
            {turn.korean ? (
              <>
                <div role="group" aria-label="답 언어" className="segmented">
                  <button type="button" aria-pressed={turn.lang === "ko"} onClick={() => onLang("ko")}>
                    한국어
                  </button>
                  <button type="button" aria-pressed={turn.lang === "en"} onClick={() => onLang("en")}>
                    원문 (English)
                  </button>
                </div>
                {turn.translation === "pending" && <span className="hint">옮기는 중…</span>}
              </>
            ) : turn.translation === "pending" ? (
              <span className="hint">한국어로 옮기는 중… 아래는 영어 원문입니다.</span>
            ) : turn.translation === "too-long" ? (
              <span className="hint">답이 길어 번역하지 않았습니다.</span>
            ) : (
              <button type="button" className="btn" onClick={onKorean}>
                {turn.translation === "failed" ? "번역하지 못했습니다 · 다시 시도" : "한국어로 보기"}
              </button>
            )}
          </div>
        )}

        {showKorean ? (
          <Markdown text={turn.korean!} large />
        ) : (
          (a?.answer ?? turn.partial) && <Markdown text={a?.answer ?? turn.partial} large />
        )}
        {showKorean && <p className="hint">답은 영어로 만들고 한국어로 옮겼습니다. 옮긴 문장이 어색하면 원문을 보세요.</p>}
      </article>

      {a && rest.length === 0 && inTable.length > 0 && (
        <details className="card fold">
          <summary>
            근거 논문 {inTable.length}편 <span className="hint">· 위 표에 모두 있습니다. 저자와 함께 보려면 펼치세요.</span>
          </summary>
          <ul className="paper-cards">
            {inTable.map((id) => (
              <li key={id}>
                <PaperCard id={id} paper={titles.get(id)} loading={!turn.subgraph} />
              </li>
            ))}
          </ul>
          {capNote && <p className="callout" style={{ marginTop: 12 }}>{capNote}</p>}
        </details>
      )}

      {a && rest.length > 0 && (
        <section aria-labelledby="ev-h" className="stack-sm">
          <div className="section-head">
            <h2 id="ev-h">{inTable.length ? "답의 표에 없는 근거 논문" : "답의 근거가 된 논문"}</h2>
            <span className="hint">
              {[
                evidence.length < rest.length ? `${rest.length}편 중 ${evidence.length}편` : `${rest.length}편`,
                inTable.length ? `표에 있는 ${inTable.length}편은 뺐습니다` : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
          <ul className="paper-cards">
            {evidence.map((id) => (
              <li key={id}>
                <PaperCard id={id} paper={titles.get(id)} loading={!turn.subgraph} />
              </li>
            ))}
          </ul>
          {(capNote || rest.length > EVIDENCE_PREVIEW) && (
            <div className="callout">
              <span style={{ flex: "1 1 320px" }}>{capNote || "근거 논문을 모두 펼쳐 볼 수 있습니다."}</span>
              {rest.length > EVIDENCE_PREVIEW && (
                <button type="button" className="btn" aria-expanded={allEvidence} onClick={() => setAllEvidence((v) => !v)}>
                  {allEvidence ? "5편만 보기" : `${rest.length}편 모두 보기`}
                </button>
              )}
            </div>
          )}
        </section>
      )}

      {a && a.related.length > 0 && (
        <section aria-labelledby="next-h" className="stack-sm">
          <div>
            <h2 id="next-h">이어서 읽을 만한 논문</h2>
            <p className="hint">근거 논문들과 함께 자주 인용된 논문입니다.</p>
          </div>
          <ul className="card link-list">
            {a.related.map((r) => (
              <li key={r.arxiv_id}>
                <Link href={`/papers/${r.arxiv_id}`}>{r.title ?? titles.get(r.arxiv_id)?.title ?? r.arxiv_id}</Link>
                <span className="hint">{r.arxiv_id}</span>
              </li>
            ))}
          </ul>
          {forecastSeed(a) && <ForecastPanel key={forecastSeed(a)} id={forecastSeed(a)!} />}
        </section>
      )}

      {a && turn.subgraph && (
        <details className="card fold">
          <summary>근거를 그래프로 보기</summary>
          <EvidenceGraph subgraph={turn.subgraph} evidence={a.evidence_ids} />
        </details>
      )}

      {a && <HowFound answer={a} ms={turn.ms} />}
    </div>
  );
}

function PaperCard({ id, paper, loading }: { id: string; paper?: SubgraphPaper; loading: boolean }) {
  return (
    <Link href={`/papers/${id}`} className="paper-card">
      <span className="paper-title">{paper?.title ?? (loading ? "제목을 읽는 중…" : id)}</span>
      <span className="hint">{paperLine(paper, id)}</span>
    </Link>
  );
}

// 개발자가 보는 기록. 답과 같은 높이에 두지 않고, 접어서 맨 아래에 둡니다(ADR-108).
function HowFound({ answer: a, ms }: { answer: Answer; ms?: number }) {
  const evidence = citedEvidence(a);
  const stages = stageRows(a);
  return (
    <details className="card fold">
      <summary>
        어떻게 찾았나 <span className="hint">· {howLine(a, ms)}</span>
      </summary>
      <div className="stack-sm" style={{ paddingTop: 8 }}>
        {a.cyphers.length ? (
          <>
            <p className="lbl">그래프에 보낸 조회문 (Cypher)</p>
            {a.cyphers.map((c, i) => (
              <pre key={i} className="cypher mono">
                {c}
              </pre>
            ))}
          </>
        ) : (
          <p className="hint">그래프 조회 없이 의미 검색으로 답했습니다.</p>
        )}
        <dl className="facts">
          <dt>돌아온 행</dt>
          <dd className="mono">{a.rows.length}</dd>
          <dt>조회 결과에 있던 근거</dt>
          <dd className="mono">
            {evidence.found} / {evidence.total}
          </dd>
          <dt>걸린 시간</dt>
          <dd className="mono">{ms ? seconds(ms) : "—"}</dd>
          {attemptsOf(a) > 1 && (
            <>
              <dt>시도</dt>
              <dd className="mono">{attemptsOf(a)}회</dd>
            </>
          )}
        </dl>
        {stages.length > 0 && (
          <>
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
            <p className="hint">서버가 단계마다 잰 시간입니다. 위의 &quot;걸린 시간&quot;은 브라우저가 잰 것이라 네트워크만큼 더 깁니다.</p>
          </>
        )}
        {a.tool_calls.length > 0 && (
          <>
            <p className="lbl">도구를 부른 순서</p>
            <ol className="mono" style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
              {a.tool_calls.map((c) => (
                <li key={c.id}>
                  {c.name}
                  {c.status && c.status !== "success" ? ` (${c.status})` : ""}
                </li>
              ))}
            </ol>
          </>
        )}
        {a.papers.length > 0 && <VectorHits hits={a.papers} />}
      </div>
    </details>
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
