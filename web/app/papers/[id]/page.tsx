import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CitingList } from "@/components/CitingList";
import { ForecastPanel } from "@/components/ForecastPanel";
import { PaperActions } from "@/components/PaperActions";
import { PaperSearch } from "@/components/PaperSearch";
import { getPaper } from "@/lib/api";
import { categoryLabel } from "@/lib/categories";
import { ARXIV_ID, askHref, authorQuestion, questionsFor, year } from "@/lib/paper";

type Props = { params: Promise<{ id: string }> };

const RANKED_BY = { cocitation: "함께 인용", pagerank: "인용망 근접", embedding: "초록 유사" } as const;

async function load(id: string) {
  return ARXIV_ID.test(id) ? getPaper(id) : null;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const paper = await load((await params).id);
  return { title: paper ? `${paper.title} · arXiv GraphRAG` : "논문 · arXiv GraphRAG" };
}

export default async function PaperPage({ params }: Props) {
  const paper = await load((await params).id);
  if (!paper) notFound();
  const short = paper.title.split(":")[0].trim();

  return (
    <main className="page stack">
      <section className="card panel">
        <PaperSearch compact />
      </section>

      <article className="card panel paper-head">
        <p className="meta">
          {[categoryLabel(paper.primary_category), paper.published ? year(paper.published) : "", `arXiv ${paper.arxiv_id}`]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <h1>{paper.title}</h1>
        {paper.authors.length > 0 && (
          <p className="authors">
            {paper.authors.map((name, i) => (
              <span key={name}>
                {i > 0 && ", "}
                <Link href={askHref(authorQuestion(name))} title={`${name}의 논문을 묻는 질문으로`}>
                  {name}
                </Link>
              </span>
            ))}
          </p>
        )}
        {/* 초록을 접지 않습니다. 논문 페이지에 온 사람이 가장 먼저 읽는 것입니다(ADR-109). */}
        {paper.abstract && <p className="abstract">{paper.abstract}</p>}
        <div className="actions">
          <a className="btn btn-link" href={`https://arxiv.org/abs/${paper.arxiv_id}`} target="_blank" rel="noreferrer">
            arXiv에서 보기
          </a>
          {paper.pdf_url && (
            <a className="btn btn-link" href={paper.pdf_url} target="_blank" rel="noreferrer">
              PDF
            </a>
          )}
        </div>
        <PaperActions paper={paper} />
        <dl className="tiles">
          <div>
            <dt>이 논문을 인용한 논문 (그래프 안)</dt>
            <dd className="mono">{paper.cited_by.toLocaleString("ko-KR")}</dd>
          </div>
          <div>
            <dt>저장된 참고문헌</dt>
            <dd className="mono">{paper.references.toLocaleString("ko-KR")}</dd>
          </div>
          <div>
            <dt>저자</dt>
            <dd className="mono">{paper.authors.length}</dd>
          </div>
        </dl>
        {paper.references === 0 && (
          // 대부분의 논문이 그렇습니다. 경고 상자 대신 한 줄로 적습니다(ADR-109).
          <p className="hint">
            이 논문의 참고문헌은 그래프에 없습니다. 인용한 논문이 없다는 뜻은 아니지만, &quot;이 논문이 인용한 논문&quot;은 답하지 못합니다.
          </p>
        )}
      </article>

      <div className="two-col">
        <section className="card panel">
          <h2>함께 인용되는 논문</h2>
          <p className="hint">
            {short}와 같은 논문에 함께 인용된 횟수입니다. 인기 순위가 아니라 같이 읽히는 순서입니다.
          </p>
          {paper.related.length === 0 ? (
            <p className="hint">저장된 인용 관계가 없어 찾지 못했습니다.</p>
          ) : (
            <ol className="plain-list numbered">
              {paper.related.map((r) => (
                <li key={r.arxiv_id}>
                  <Link href={`/papers/${r.arxiv_id}`}>{r.title}</Link>
                  <span className="mono sub">
                    {r.shared_citers ? `${r.shared_citers}회` : RANKED_BY[r.ranked_by]} · {r.arxiv_id}
                  </span>
                </li>
              ))}
            </ol>
          )}
          <ForecastPanel id={paper.arxiv_id} />
        </section>
        <CitingList id={paper.arxiv_id} total={paper.cited_by} />
      </div>

      <section className="card panel">
        <h2>이 논문에서 시작하는 질문</h2>
        <p className="hint">누르면 질문 칸이 채워집니다. 보내기는 직접 누릅니다.</p>
        <div className="actions">
          {questionsFor(paper).map((q) => (
            <Link key={q.label} className="btn btn-link" href={askHref(q.question)}>
              {q.label}
            </Link>
          ))}
        </div>
      </section>
    </main>
  );
}
