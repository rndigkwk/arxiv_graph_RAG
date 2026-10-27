import type { Metadata } from "next";
import { FAMILY_LABEL, type Future, atCeiling, floorText, getQuality, judged, num, yesNo } from "@/lib/quality";

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
        {q.basis && q.basis.length > 0 && (
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 500 }}>RAGAS 하한이 무언가를 막는지 — 결함을 심어 확인</summary>
            <p className="route">
              물을 것은 &quot;평균이 하한보다 큰가&quot;가 아닙니다(ADR-004·ADR-043). <b>정상이던 실행 중 최악이 하한을 넘는가</b>,
              그리고 <b>결함을 심으면 걸리는가</b>입니다. 둘째가 아니면 그 하한은 상수에 걸려 있습니다.
            </p>
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>지표</th>
                    <th>하한</th>
                    <th>본 실행</th>
                    <th>최악 정상 실행</th>
                    <th>하한까지 여유</th>
                    <th>최악도 넘는가</th>
                    <th>결함을 심으면</th>
                    <th>그때 걸리는가</th>
                  </tr>
                </thead>
                <tbody>
                  {q.basis.map((b) => (
                    <tr key={b.metric}>
                      <td className="mono" style={{ fontSize: 13 }}>
                        {b.metric}
                      </td>
                      <td className="mono">{num(b.floor)}</td>
                      <td className="mono">{b.runs_seen}</td>
                      <td className="mono">{num(b.worst_normal_run)}</td>
                      <td className="mono">{num(b.margin)}</td>
                      <td>{yesNo(b.survives)}</td>
                      <td className="mono">{b.with_defect.map((v) => v.toFixed(4)).join(", ")}</td>
                      <td>{yesNo(b.defect_trips_it)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
        {q.prompts && q.prompts.length > 0 && (
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 500 }}>이 점수를 낸 프롬프트</summary>
            <p className="route">
              <span className="mono">sha256</span> 앞 12자입니다. <b>지문이 다르면 다른 프롬프트</b>이고, 게이트의{" "}
              <span className="mono">prompt_version</span> 규칙이 막습니다 — <span className="mono">git_sha</span>로는 대신할 수
              없습니다(ADR-018·ADR-041).
            </p>
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>프롬프트</th>
                    <th>기준선</th>
                    <th>지금 코드</th>
                    <th>같은가</th>
                  </tr>
                </thead>
                <tbody>
                  {q.prompts.map((r) => (
                    <tr key={r.prompt}>
                      <td className="mono" style={{ fontSize: 13 }}>
                        {r.prompt}
                      </td>
                      <td className="mono">{r.baseline ?? "—"}</td>
                      <td className="mono">{r.code ?? "—"}</td>
                      <td>{yesNo(r.same)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
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

      {q.future && q.future.variants.length > 0 && <FutureStudy future={q.future} />}

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

// 관련 논문 변형 비교. 배포된 경로(현직)와 겨룬 결과이고, 연결 정도로 나눈 표가 붙습니다.
function FutureStudy({ future }: { future: Future }) {
  return (
    <section className="card panel">
      <h2>관련 논문 변형 비교 — 미래 라벨 과제</h2>
      <p className="meta mono">
        시드 {future.cases}건 · 피처 창 {future.feature_cut} 이전 · 배포된 경로와 겨룹니다
      </p>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>변형</th>
              <th>recall@5</th>
              <th>hit@5</th>
              <th>MRR</th>
              <th>배포 대비 recall@5 (95% 구간)</th>
            </tr>
          </thead>
          <tbody>
            {future.variants.map((v) => (
              <tr key={v.variant}>
                <td className="mono">
                  {v.deployed ? <b>{v.variant}</b> : v.variant} {v.deployed && <span className="tag">배포</span>}
                </td>
                <td className="mono">{num(v.recall_at_k, 4)}</td>
                <td className="mono">{num(v.hit_at_k, 4)}</td>
                <td className="mono">{num(v.reciprocal_rank, 4)}</td>
                <td className="mono">{v.vs_incumbent || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {future.degrees.length > 0 && (
        <>
          <h3 style={{ fontSize: 15, margin: "8px 0 0" }}>연결 정도로 나누면</h3>
          <p className="route">
            <b>집계만 보면 두 체제가 섞입니다</b>(ADR-037). 시드의 인용 간선 수로 나눈 recall@5입니다.
          </p>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>시드</th>
                  <th>수</th>
                  <th>비율</th>
                  {future.degree_variants.map((v) => (
                    <th key={v} className="mono">
                      {v}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {future.degrees.map((d) => (
                  <tr key={d.group}>
                    <td>{d.group}</td>
                    <td className="mono">{d.seeds}</td>
                    <td className="mono">{(d.share * 100).toFixed(1)}%</td>
                    {future.degree_variants.map((v) => (
                      <td key={v} className="mono">
                        {typeof d[v] === "number" ? (d[v] as number).toFixed(4) : "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
