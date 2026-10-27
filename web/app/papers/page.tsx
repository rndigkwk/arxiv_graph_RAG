import type { Metadata } from "next";
import { PaperSearch } from "@/components/PaperSearch";
import { SavedList } from "@/components/SavedList";

export const metadata: Metadata = { title: "논문 · arXiv GraphRAG" };

export default function Papers() {
  return (
    <main className="page stack">
      <section className="card panel">
        <PaperSearch />
        <p className="route">제목 구절이나 arXiv ID로 찾습니다. 전문 색인이라 모델을 부르지 않습니다(0원).</p>
      </section>
      <SavedList />
    </main>
  );
}
