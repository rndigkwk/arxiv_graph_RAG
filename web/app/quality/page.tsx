import type { Metadata } from "next";
import { FAMILY_LABEL, atCeiling, floorText, getQuality, judged, num } from "@/lib/quality";

export const metadata: Metadata = { title: "품질 · arXiv GraphRAG" };

const REPO = "https://github.com/rndigkwk/arxiv_graph_RAG/blob/main";

// 게이트·하한·기각 기록을 한 화면에. 여기서 숫자를 만들지 않습니다(`src/quality_board.py`가 읽습니다).
export default async function QualityPage() {
  const q = await getQuality();
  if (!q) {
    return (
      <main className="page">
        <p className="note">품질 데이터를 읽지 못했습니다. API가 잠시 내려가 있을 수 있습니다.</p>
      </main>
    );
  }
  const s = q.summary;
  const gates = q.gates.filter((g) => g.gated);

  return (
    <main className="page stack">
      <section className="card panel">
        <h1>품질 게이트</h1>
        <p className="meta mono">
          기준선 {s.generated_at.slice(0, 10)} · {s.dataset} {s.cases}건 × {s.runs}회 · RAGAS 판정 {s.judge_model}
        </p>
        {s.prompts_drifted.length > 0 && (
          <p className="note">
            지금 코드의 프롬프트가 기준선을 만든 실행과 다릅니다: {s.prompts_drifted.join(", ")} — 아래 숫자는{" "}
            <b>이 코드가 낸 점수가 아닙니다.</b>
          </p>
        )}
        <dl className="tiles">
          <div>
            <dt>게이트가 보는 지표</dt>
            <dd className="mono">{s.gated_metrics}</dd>
            <dd className="hint">하나라도 하한 아래면 머지되지 않습니다</dd>
          </div>
          <div>
            <dt>기준선</dt>
            <dd className="mono">
              {s.cases} × {s.runs}
            </dd>
            <dd className="hint">문항 × 실행 · {s.generated_at.slice(0, 10)}</dd>
          </div>
          <div>
            <dt>변동폭보다 좁은 하한</dt>
            <dd className="mono">{s.tight_floors.length}</dd>
            <dd className="hint">
              {s.tight_floors.length ? s.tight_floors.join(", ") : "하한이 운을 재고 있지 않습니다"}
            </dd>
          </div>
        </dl>
      </section>

      <section className="card panel">
        <h2>지표 · 하한 · 여유</h2>
        <p className="route">
          <b>여유가 3회 변동폭보다 좁으면 그 하한은 품질이 아니라 운을 잽니다</b>(ADR-004). 둘을 나란히 둡니다.
        </p>
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>지표</th>
                <th>측정</th>
                <th>하한</th>
                <th>여유</th>
                <th>허용 하락폭</th>
                <th>3회 변동폭</th>
              </tr>
            </thead>
            <tbody>
              {gates.map((g) => (
                <tr key={g.metric}>
                  <td className="mono" style={{ fontSize: 13 }}>
                    {g.metric}
                  </td>
                  <td className="mono">{num(g.measured)}</td>
                  <td className="mono">{floorText(g)}</td>
                  <td className="mono">{num(g.headroom)}</td>
                  <td className="mono">{num(g.allowed_drop)}</td>
                  <td className="mono">{num(g.observed_spread)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 500 }}>하한을 그 값으로 정한 이유</summary>
          <dl className="facts" style={{ marginTop: 10, gridTemplateColumns: "1fr" }}>
            {Object.entries(q.rationale ?? {}).map(([name, why]) => (
              <div key={name} style={{ marginBottom: 8 }}>
                <dt className="mono">{name}</dt>
                <dd>{why}</dd>
              </div>
            ))}
          </dl>
        </details>
      </section>

      <section className="card panel">
        <h2>규칙 — 하나라도 걸리면 머지되지 않습니다</h2>
        <table>
          <tbody>
            {q.rules.map((r) => (
              <tr key={r.name}>
                <td className="mono" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
                  {r.name}
                </td>
                <td>{r.blocks}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {q.families && q.families.length > 0 && (
        <section className="card panel">
          <h2>분야별 — 분야마다 {q.families[0].cases}문항 × {q.families[0].runs}회</h2>
          {atCeiling(q.families) && (
            <p className="route">
              <b>검색·근거 지표는 다섯 분야 모두 1.0이고 실패는 0건입니다.</b> 이 문항으로는 그 지표에서 분야를 가르지
              못합니다. 분야끼리 다른 것은 RAGAS뿐이고, context_precision은 &quot;게이트&quot;가 &quot;예&quot;인 분야만 봅니다(ADR-017).
            </p>
          )}
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>분야</th>
                  <th>n</th>
                  <th>hit@5</th>
                  <th>근거 재현율</th>
                  <th>실패율</th>
                  <th>faithfulness</th>
                  <th>answer_relevancy</th>
                  <th>context_precision</th>
                  <th>게이트</th>
                </tr>
              </thead>
              <tbody>
                {q.families.map((f) => (
                  <tr key={f.family}>
                    <td>{FAMILY_LABEL[f.family] ?? f.family}</td>
                    <td className="mono">
                      {f.cases}×{f.runs}
                    </td>
                    <td className="mono">{num(f.hit_at_k, 2)}</td>
                    <td className="mono">{num(f.answer_evidence_recall, 2)}</td>
                    <td className="mono">{num(f.failure_rate, 3)}</td>
                    <td className="mono">{judged(f, "faithfulness")}</td>
                    <td className="mono">{judged(f, "answer_relevancy")}</td>
                    <td className="mono">{judged(f, "context_precision")}</td>
                    <td>{f.gated_context_precision ? "예" : "아니오"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="two-col">
        <section className="card panel">
          <h2>평가 하네스 밖에서 지키는 선</h2>
          <ul className="plain-list">
            {(q.slots ?? []).map((slot) => (
              <li key={slot.label}>
                {slot.label} · <span className="mono">{slot.metric}</span>
                {slot.variant && <span className="mono"> ({slot.variant})</span>} — 측정 <b>{slot.measured}</b>, 하한{" "}
                <b>{slot.floor}</b>
                <span className="mono sub">검사: {slot.checked_by}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="card panel">
          <h2>재서 기각한 모델</h2>
          <p className="route">무엇을 쓰는지만큼 무엇을 왜 안 쓰는지가 이 저장소의 내용입니다.</p>
          <ul className="plain-list">
            {(q.rejected ?? []).map((r) => (
              <li key={r.model}>
                {r.model} <span className="tag">{r.slot}</span>
                <span className="sub">
                  {r.result} · <span className="mono">{r.record}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="route">
            전체 기록: <a href={`${REPO}/docs/MODELS.md`}>docs/MODELS.md</a> ·{" "}
            <a href="https://github.com/rndigkwk/arxiv_graph_RAG/tree/main/docs/adr">결정 기록</a>
          </p>
        </section>
      </div>
    </main>
  );
}
