import Link from "next/link";
import { PaperSearch } from "@/components/PaperSearch";

export default function NotFound() {
  return (
    <main className="page stack">
      <section className="card panel">
        <h1 style={{ margin: 0, fontSize: 18 }}>이 그래프에 없는 논문입니다</h1>
        <p className="route">
          arXiv ID가 맞는지 확인하거나 제목으로 찾아 보세요. 그래프 밖의 논문은 <Link href="/">질문</Link>으로도 답하지 못합니다.
        </p>
        <PaperSearch />
      </section>
    </main>
  );
}
