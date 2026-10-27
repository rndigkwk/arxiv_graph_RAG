import type { Metadata } from "next";
import { Explorer } from "@/components/Explorer";
import { ARXIV_ID } from "@/lib/explore";

export const metadata: Metadata = { title: "탐색 · arXiv GraphRAG" };

type Props = { searchParams: Promise<{ seed?: string | string[] }> };

// `?seed=`로 오면 그 논문에서 출발한 개인화 PageRank로 엽니다.
export default async function Explore({ searchParams }: Props) {
  const { seed } = await searchParams;
  return (
    <main className="page">
      <Explorer initialSeed={typeof seed === "string" && ARXIV_ID.test(seed) ? seed : ""} />
    </main>
  );
}
