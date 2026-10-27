import { API_BASE } from "@/lib/api";
import { Nav } from "./Nav";

type Health = { commit: string | null };
type Stats = { counts: { papers: number; references: number } };

async function load<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(API_BASE + path, { next: { revalidate: 600 } });
    return response.ok ? ((await response.json()) as T) : null;
  } catch {
    return null; // VM이 잠시 내려가 있어도 머리줄 때문에 화면 전체가 실패하지 않게 합니다.
  }
}

export async function SiteHeader() {
  const [stats, health] = await Promise.all([load<Stats>("/api/stats"), load<Health>("/api/health")]);
  return (
    <header className="site-header">
      <a href="/" className="brand">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
          <g fill="none" stroke="var(--accent)" strokeWidth="2">
            <circle cx="5" cy="6" r="3" />
            <circle cx="17" cy="5" r="3" />
            <circle cx="11" cy="17" r="3" />
          </g>
          <path d="M7.5 7.5 9.5 14.5M15.5 7.5 12.5 14.5M8 6h6" stroke="var(--accent)" strokeWidth="1.5" />
        </svg>
        arXiv GraphRAG
      </a>
      <Nav />
      <div className="stats mono">
        {stats && <span>논문 {stats.counts.papers.toLocaleString("ko-KR")}편</span>}
        {stats && <span>인용 {stats.counts.references.toLocaleString("ko-KR")}</span>}
        {health?.commit && (
          <a href={`https://github.com/rndigkwk/arxiv_graph_RAG/commit/${health.commit}`} title="답을 내는 API의 커밋">
            배포 {health.commit}
          </a>
        )}
      </div>
    </header>
  );
}
