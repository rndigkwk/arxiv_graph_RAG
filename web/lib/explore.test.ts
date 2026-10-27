import { describe, expect, it } from "vitest";
import type { RankedPaper } from "./api";
import { barShare, rankQuery, rankingCsv } from "./explore";

const paper = (over: Partial<RankedPaper> = {}): RankedPaper => ({
  rank: 1, score: 0.0006, arxiv_id: "2210.03629", title: "ReAct", primary_category: "cs.CL",
  published: "2022-10-06T01:00:32Z", cited_by: 480, ...over,
});

describe("explore helpers", () => {
  it("sends the scope only where it means something", () => {
    const base = { includeSecondary: true, scope: "subgraph" as const, skip: 0 };
    expect(rankQuery({ ...base, by: "cited" })).toBe("by=cited&skip=0&limit=20");
    expect(rankQuery({ ...base, by: "pagerank", category: "cs.AI" })).toBe(
      "by=pagerank&skip=0&limit=20&category=cs.AI&include_secondary=true&scope=subgraph",
    );
    expect(rankQuery({ ...base, by: "cited", category: "cs.AI", skip: 20 })).toBe(
      "by=cited&skip=20&limit=20&category=cs.AI&include_secondary=true",
    );
    expect(rankQuery({ ...base, by: "personalized", seed: "2210.03629" })).toContain("seed=2210.03629");
  });

  it("quotes titles that would break a CSV row", () => {
    const csv = rankingCsv("cited", [paper({ title: 'A "quoted", title' })]);
    expect(csv.split("\n")[0]).toBe("rank,arxiv_id,title,primary_category,published,cited_by_in_graph");
    expect(csv).toContain('"A ""quoted"", title"');
    expect(rankingCsv("pagerank", [paper()]).split("\n")[0].endsWith(",score")).toBe(true);
  });

  it("scales bars to the top of the list and keeps tiny ones visible", () => {
    expect(barShare(240, 480)).toBe(0.5);
    expect(barShare(1, 1000)).toBe(0.01);
    expect(barShare(3, 0)).toBe(0);
  });
});
