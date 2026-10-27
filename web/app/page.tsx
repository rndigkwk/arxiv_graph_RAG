import { Workbench } from "@/components/Workbench";

type Props = { searchParams: Promise<{ q?: string | string[] }> };

export default async function Home({ searchParams }: Props) {
  const { q } = await searchParams;
  return (
    <main className="page">
      <Workbench initial={typeof q === "string" ? q.slice(0, 2000) : ""} />
    </main>
  );
}
