import type { Metadata } from "next";
import { type Counts, type Overview, getOverview } from "@/lib/api";
import { PAPER_PROPERTIES, biggestLink, placeCommunities, sqrtScale } from "@/lib/overview";

export const metadata: Metadata = { title: "그래프 · arXiv GraphRAG" };

const n = (v: number) => v.toLocaleString("ko-KR");
const W = 520;
const H = 340;

// Streamlit 홈 탭을 옮긴 화면: 이 앱이 질문하는 그래프가 무엇인지. 그림은 SVG로 직접 그립니다.
export default async function GraphPage() {
  const o = await getOverview();
  if (!o) {
    return (
      <main className="page">
        <p className="note">그래프 정보를 읽지 못했습니다. API가 잠시 내려가 있을 수 있습니다.</p>
      </main>
    );
  }
  const c = o.counts;

  return (
    <main className="page stack">
      <section className="card panel">
        <h1>이 앱이 질문하는 지식 그래프</h1>
        <p className="route">
          arXiv 컴퓨터과학 논문과 그 논문들이 인용한 논문을 Neo4j에 적재한 그래프입니다. 질문 화면에서 이 그래프에 묻고, 탐색
          화면에서 인용망의 중심을 봅니다.
        </p>
        <dl className="tiles">
          <div>
            <dt>논문</dt>
            <dd className="mono">{n(c.papers)}</dd>
          </div>
          <div>
            <dt>저자 이름</dt>
            <dd className="mono">{n(c.authors)}</dd>
          </div>
          <div>
            <dt>AUTHORED</dt>
            <dd className="mono">{n(c.authored)}</dd>
          </div>
          <div>
            <dt>REFERENCES</dt>
            <dd className="mono">{n(c.references)}</dd>
          </div>
        </dl>
      </section>

      <section className="card panel">
        <h2>스키마 메타그래프</h2>
        <p className="route">라벨 하나가 점 하나입니다. 점 크기와 선 굵기는 지금 그래프에서 센 실제 개수입니다(제곱근 비례).</p>
        <Schema counts={c} />
        <pre className="cypher mono">
          {"(:ArxivAuthorName {name})-[:AUTHORED]->(:ArxivPaper)\n(:ArxivPaper)-[:REFERENCES]->(:ArxivPaper)"}
        </pre>
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 500 }}>ArxivPaper의 속성</summary>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>속성</th>
                  <th>타입</th>
                  <th>설명</th>
                </tr>
              </thead>
              <tbody>
                {PAPER_PROPERTIES.map((p) => (
                  <tr key={p.name}>
                    <td className="mono">{p.name}</td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {p.type}
                    </td>
                    <td>{p.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="route">
            분류는 노드가 아니라 ArxivPaper의 속성이라 :ArxivCategory 노드는 없습니다. ArxivAuthorName은 이름 표기 노드라
            동명이인을 구분하지 않습니다.
          </p>
        </details>
      </section>

      {o.communities ? (
        <Communities data={o.communities} />
      ) : (
        <p className="note">커뮤니티 보고서(reports/aura_graph_rag_quality_report.json)가 없어 커뮤니티 그림을 그리지 않습니다.</p>
      )}
    </main>
  );
}

function Schema({ counts }: { counts: Counts }) {
  const bigNode = Math.max(counts.papers, counts.authors);
  const bigEdge = Math.max(counts.authored, counts.references);
  const author = { x: 130, y: 150, r: sqrtScale(counts.authors, bigNode, 22, 62) };
  const paper = { x: 360, y: 150, r: sqrtScale(counts.papers, bigNode, 22, 62) };
  const authored = sqrtScale(counts.authored, bigEdge, 1.5, 9);
  const references = sqrtScale(counts.references, bigEdge, 1.5, 9);
  return (
    <svg viewBox={`0 0 ${W} 260`} role="img" aria-label="ArxivAuthorName이 ArxivPaper를 AUTHORED로, ArxivPaper가 ArxivPaper를 REFERENCES로 잇는 스키마" style={{ width: "100%", maxWidth: W }}>
      <line x1={author.x + author.r} y1={150} x2={paper.x - paper.r} y2={150} stroke="var(--muted)" strokeWidth={authored} />
      <text x={(author.x + author.r + paper.x - paper.r) / 2} y={136} textAnchor="middle" fontSize={12} fill="var(--muted)">
        AUTHORED {n(counts.authored)}
      </text>
      {/* 자기 자신으로 돌아오는 REFERENCES 고리 */}
      <path
        d={`M ${paper.x + paper.r * 0.5} ${150 - paper.r * 0.87} C ${paper.x + 150} ${40}, ${paper.x + 150} ${260}, ${paper.x + paper.r * 0.5} ${150 + paper.r * 0.87}`}
        fill="none"
        stroke="var(--muted)"
        strokeWidth={references}
      />
      <text x={paper.x + 136} y={150} textAnchor="middle" fontSize={12} fill="var(--muted)" transform={`rotate(90 ${paper.x + 136} 150)`}>
        REFERENCES {n(counts.references)}
      </text>
      <circle cx={author.x} cy={150} r={author.r} fill="var(--warm)" opacity={0.85} />
      <circle cx={paper.x} cy={150} r={paper.r} fill="var(--accent)" opacity={0.85} />
      <text x={author.x} y={150 + author.r + 18} textAnchor="middle" fontSize={13} fill="var(--ink)">
        ArxivAuthorName {n(counts.authors)}
      </text>
      <text x={paper.x} y={150 + paper.r + 18} textAnchor="middle" fontSize={13} fill="var(--ink)">
        ArxivPaper {n(counts.papers)}
      </text>
    </svg>
  );
}

function Communities({ data }: { data: NonNullable<Overview["communities"]> }) {
  const placed = placeCommunities(data.rows, W, H);
  const at = new Map(placed.map((p) => [p.label, p]));
  const biggest = biggestLink(data);
  return (
    <section className="card panel">
      <h2>커뮤니티 메타그래프</h2>
      <p className="route">
        인용망을 Leiden으로 나눈 커뮤니티 중 상위 5개입니다. 점 하나가 커뮤니티 하나이고, 점 크기는 논문 수, 선 굵기는 두
        커뮤니티 사이의 상호 인용 수입니다. <b>지금 그래프가 아니라 보고서를 만든 날의 그래프입니다</b> — VM의 Neo4j에는 GDS가 없어
        다시 계산하지 않습니다.
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="상위 5개 커뮤니티와 그 사이의 상호 인용" style={{ width: "100%", maxWidth: W }}>
        {data.links.map((l) => {
          const a = at.get(l.a)!;
          const b = at.get(l.b)!;
          return (
            <line key={l.a + l.b} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--muted)" strokeOpacity={0.6} strokeWidth={sqrtScale(l.citations, biggest, 1, 9)}>
              <title>
                {l.a}–{l.b} 상호 인용 {n(l.citations)}
              </title>
            </line>
          );
        })}
        {placed.map((p) => (
          <g key={p.label}>
            <circle cx={p.x} cy={p.y} r={p.r} fill="var(--accent)" opacity={0.85}>
              <title>
                {p.label} · 논문 {n(p.papers)} · 안쪽 인용 {n(p.inside)}
              </title>
            </circle>
            <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize={13} fontWeight={600} fill="#fff">
              {p.label}
            </text>
            <text x={p.x} y={p.y + p.r + 15} textAnchor="middle" fontSize={11} fill="var(--muted)">
              {p.primary_category}
            </text>
          </g>
        ))}
      </svg>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>커뮤니티</th>
              <th>논문</th>
              <th>안쪽 인용</th>
              <th>주분류</th>
              <th>보조분류</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.label}>
                <td className="mono">{r.label}</td>
                <td className="mono">{n(r.papers)}</td>
                <td className="mono">{n(r.inside)}</td>
                <td className="mono">{r.primary_category}</td>
                <td className="mono">{r.secondary_category ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="meta mono">
        출처 reports/aura_graph_rag_quality_report.json · 생성 {data.generated_at.slice(0, 10)} · Leiden {data.mode}
      </p>
    </section>
  );
}
