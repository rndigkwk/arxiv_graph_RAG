import type { Subgraph } from "@/lib/api";

// 근거 논문(왼쪽)과 이어서 읽을 논문(오른쪽), 그 사이의 **저장된** 인용 관계만 그립니다.
// 시안의 점선("함께 인용됨")은 어느 근거와 이어지는지 응답에 없어서 그리지 않습니다.

const ROW = 40;
const BOX_W = 92;

export function EvidenceGraph({ subgraph, evidence }: { subgraph: Subgraph; evidence: string[] }) {
  const known = new Set(subgraph.papers.map((p) => p.arxiv_id));
  const left = evidence.filter((id) => known.has(id));
  const right = subgraph.papers.map((p) => p.arxiv_id).filter((id) => !left.includes(id));
  if (!left.length && !right.length) return null;

  const height = Math.max(left.length, right.length, 1) * ROW + 20;
  const pos = new Map<string, { x: number; y: number }>();
  left.forEach((id, i) => pos.set(id, { x: 40, y: 20 + i * ROW }));
  right.forEach((id, i) => pos.set(id, { x: 420, y: 20 + i * ROW }));
  const title = new Map(subgraph.papers.map((p) => [p.arxiv_id, p.title]));
  const edges = subgraph.references.filter((e) => pos.has(e.citing) && pos.has(e.cited));

  return (
    <figure style={{ margin: 0, padding: 12, background: "var(--ground)", borderRadius: 8 }}>
      <svg
        viewBox={`0 0 560 ${height}`}
        width="100%"
        role="img"
        aria-label={`근거 ${left.length}편과 이어서 읽을 논문 ${right.length}편, 그 사이 인용 ${edges.length}개`}
      >
        <defs>
          <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0 8 4 0 8z" fill="var(--accent)" />
          </marker>
        </defs>
        {edges.map((e) => {
          const a = pos.get(e.citing)!;
          const b = pos.get(e.cited)!;
          const ax = a.x < b.x ? a.x + BOX_W : a.x;
          const bx = a.x < b.x ? b.x : a.x === b.x ? b.x : b.x + BOX_W;
          return (
            <line
              key={`${e.citing}-${e.cited}`}
              x1={ax}
              y1={a.y + 14}
              x2={bx}
              y2={b.y + 14}
              stroke="var(--accent)"
              strokeWidth={1.5}
              markerEnd="url(#arrow)"
            />
          );
        })}
        {[...pos].map(([id, p]) => {
          const isEvidence = left.includes(id);
          return (
            <a key={id} href={`/papers/${id}`}>
              <title>{title.get(id) ?? id}</title>
              <rect
                x={p.x}
                y={p.y}
                width={BOX_W}
                height={28}
                rx={4}
                fill={isEvidence ? "var(--accent-soft)" : "#fff"}
                stroke={isEvidence ? "var(--accent)" : "var(--warm)"}
              />
              <text x={p.x + BOX_W / 2} y={p.y + 18} textAnchor="middle" fontSize={11} fill="var(--ink)" className="mono">
                {id}
              </text>
            </a>
          );
        })}
      </svg>
      <figcaption style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
        <span>왼쪽: 답의 근거</span>
        <span>오른쪽: 이어서 읽을 논문</span>
        <span>
          <span style={{ color: "var(--accent)" }}>→</span> 그래프에 저장된 인용 {edges.length}개
        </span>
      </figcaption>
    </figure>
  );
}
