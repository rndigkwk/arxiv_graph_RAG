import { describe, expect, it } from "vitest";
import type { Answer } from "./api";
import { GENERATE_CONTAINS, citedEvidence, forecastSeed, generateSplit, graphIds, hasHangul, historyFor, howLine, paperLine, routeLine, rowCap, splitEvidence, stageRows, usedPath } from "./answer";

const answer = (over: Partial<Answer> = {}): Answer => ({
  question: "q",
  answer: "a",
  evidence_ids: ["2407.00215", "2509.15541"],
  tool_calls: [{ id: "1", name: "search_graph", args: {} }],
  cyphers: ["MATCH ..."],
  rows: [{ "p.arxiv_id": "2407.00215" }],
  papers: [],
  related: [{ arxiv_id: "2401.05566" }, { arxiv_id: "2407.00215" }],
  ...over,
});

describe("answer helpers", () => {
  it("seeds the forecast with the first seed of the related-paper ranking", () => {
    const ranked = (seed_ids: unknown) => ({ id: "app:rank_related_papers", name: "rank_related_papers", args: {}, output: { seed_ids } });
    expect(forecastSeed(answer({ tool_calls: [ranked(["2303.11366", "2401.1"])] }))).toBe("2303.11366");
    expect(forecastSeed(answer({ tool_calls: [ranked([])] }))).toBeNull();
    expect(forecastSeed(answer())).toBeNull(); // 관련 논문을 부르지 않은 답(순수 집계)
    expect(forecastSeed(answer({ tool_calls: [ranked("nope")] }))).toBeNull();
  });

  it("counts only evidence that the search actually returned", () => {
    expect(citedEvidence(answer())).toEqual({ found: 1, total: 2 });
    expect(citedEvidence(answer({ papers: [{ arxiv_id: "2509.15541" }] }))).toEqual({ found: 2, total: 2 });
  });

  it("names the path by the tools that ran", () => {
    expect(usedPath(answer())).toBe("그래프 경로");
    expect(usedPath(answer({ tool_calls: [{ id: "1", name: "search_papers", args: {} }] }))).toBe("넓은 경로");
  });

  it("draws evidence first and each paper once", () => {
    expect(graphIds(answer())).toEqual(["2407.00215", "2509.15541", "2401.05566"]);
  });

  it("sends at most six earlier turns, newest kept", () => {
    const turns = Array.from({ length: 8 }, (_, i) => ({ question: `q${i}`, answer: "a" }));
    expect(historyFor(turns).map((t) => t.question)).toEqual(["q2", "q3", "q4", "q5", "q6", "q7"]);
  });

  it("explains the route with its cues", () => {
    expect(routeLine({ name: "graph", cues: ["citation"], vetoes: [] })).toContain("단서 citation");
  });

  it("lists every stage slowest first, keeps zero-second ones, and ends with the total", () => {
    const rows = stageRows(
      answer({
        graph_trace: {
          seconds: { route: 0, generate: 9.5, check_format: 0.2 },
          visits: { route: 1, generate: 2, check_format: 2 },
          attempts: 2,
          total_seconds: 9.7,
        },
      }),
    );
    expect(rows.map((r) => r.node)).toEqual(["답변 생성", "형식·인용 검사", "경로 판정", "합계"]);
    expect(rows.at(-1)).toMatchObject({ seconds: 9.7, visits: 5 });
    expect(rows[0].note).toBe(GENERATE_CONTAINS);
    expect(stageRows(answer())).toEqual([]);
  });

  it("splits generation only when the parts add up", () => {
    const inside = { adds_up: true, model_seconds: 6, model_turns: 3, tools_seconds: 2.5, tool_calls: 2, unattributed_seconds: 0, other_seconds: 1 };
    expect(generateSplit(answer({ generate_trace: inside }))).toBe("모델 6.0초 (3턴) · 도구 2.5초 (2회) · 그 외 1.0초");
    expect(generateSplit(answer({ generate_trace: { ...inside, adds_up: false } }))).toBe("");
  });
});

describe("answer-first screen", () => {
  it("translates only questions asked in Korean", () => {
    expect(hasHangul("Yoshua Bengio가 쓴 논문이 있어?")).toBe(true);
    expect(hasHangul("Which papers cite ReAct?")).toBe(false);
  });

  it("says when the query stopped at its LIMIT", () => {
    const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ id: i }));
    expect(rowCap(answer({ cyphers: ["MATCH (p) RETURN p ORDER BY p.title LIMIT 25"], rows: rows(25) }))).toBe(25);
    expect(rowCap(answer({ cyphers: ["MATCH (p) RETURN p LIMIT 25"], rows: rows(7) }))).toBeNull();
    expect(rowCap(answer({ cyphers: ["MATCH (p) RETURN count(p)"], rows: rows(1) }))).toBeNull();
    expect(rowCap(answer({ cyphers: [], rows: [] }))).toBeNull();
  });

  it("summarises how the answer was found in one line", () => {
    expect(howLine(answer(), 6090)).toBe("그래프 경로 · 그래프 조회 1번 · 6.1초");
    expect(howLine(answer({ cyphers: [] }))).toBe("그래프 경로 · 의미 검색으로 찾음");
  });

  it("writes the paper line from the graph only", () => {
    expect(paperLine({ arxiv_id: "1", authors: ["A"], author_count: 3, primary_category: "cs.LG" }, "1")).toBe("A 외 · cs.LG · 1");
    expect(paperLine(undefined, "1406.2661")).toBe("1406.2661");
  });
});

describe("evidence the answer already tabled", () => {
  const ids = ["2610.20922", "2610.18511", "1406.2661"];
  it("drops cards for papers the answer's table lists", () => {
    const text = ["Yes:", "", "| arXiv ID | Title |", "|---|---|", "| 2610.20922 | A |", "| 2610.18511 | B |"].join(String.fromCharCode(10));
    expect(splitEvidence({ answer: text, evidence_ids: ids })).toEqual({ inTable: ["2610.20922", "2610.18511"], rest: ["1406.2661"] });
  });

  it("keeps papers that are only mentioned in a sentence", () => {
    expect(splitEvidence({ answer: "See 1406.2661 for GANs.", evidence_ids: ids })).toEqual({ inTable: [], rest: ids });
  });
});
