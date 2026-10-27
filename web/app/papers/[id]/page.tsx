import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CitingList } from "@/components/CitingList";
import { ForecastPanel } from "@/components/ForecastPanel";
import { PaperActions } from "@/components/PaperActions";
import { PaperSearch } from "@/components/PaperSearch";
import { getPaper } from "@/lib/api";
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
        <p className="meta mono">
          <a href={`https://arxiv.org/abs/${paper.arxiv_id}`}>arXiv {paper.arxiv_id}</a> · {paper.categories.join(" · ")}
          {paper.published ? ` · ${year(paper.published)}` : ""}
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
        <PaperActions paper={paper} />
        <dl className="tiles">
          <div>
            <dt>이 그래프에서 인용한 논문</dt>
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
          <p className="note">
            <b>이 논문의 참고문헌은 그래프에 저장되지 않았습니다.</b> 인용한 논문이 없다는 뜻이 아닙니다 — 그래프의 논문 대부분이
            참고문헌 없이 들어와 있어서, "이 논문이 인용한 논문" 질문은 빈 답이 나옵니다.
          </p>
        )}
        {paper.abstract && (
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 500 }}>초록</summary>
            <p className="answer-text" style={{ marginTop: 8 }}>
              {paper.abstract}
            </p>
          </details>
        )}
      </article>

      <div className="two-col">
        <section className="card panel">
          <h2>함께 인용되는 논문</h2>
          <p className="route">
            {short}와 같은 논문에 함께 인용된 횟수입니다. 인기 순위가 아니라 같이 읽히는 순서입니다.
          </p>
          {paper.related.length === 0 ? (
            <p className="route">저장된 인용 관계가 없어 찾지 못했습니다.</p>
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
        <p className="route">누르면 질문 칸이 채워집니다. 보내기는 직접 누릅니다.</p>
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
