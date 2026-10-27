import { describe, expect, it } from "vitest";
import { ARXIV_ID, MAX_SAVED, askHref, bibtexFile, parseSaved, questionsFor, savedCsv, savedGaps, toggleSaved } from "./paper";

const paper = (id: string) => ({ arxiv_id: id, title: `T ${id}`, bibtex: `@misc{${id},}` });

describe("paper helpers", () => {
  it("toggles a paper in and out and stops at the limit", () => {
    const one = toggleSaved([], paper("2210.03629"));
    expect(one.map((s) => s.arxiv_id)).toEqual(["2210.03629"]);
    expect(toggleSaved(one, paper("2210.03629"))).toEqual([]);
    const full = Array.from({ length: MAX_SAVED }, (_, i) => paper(`2401.${String(i).padStart(5, "0")}`));
    expect(toggleSaved(full, paper("2210.03629"))).toHaveLength(MAX_SAVED);
  });

  it("reads a broken store as empty", () => {
    expect(parseSaved(null)).toEqual([]);
    expect(parseSaved("{not json")).toEqual([]);
    expect(parseSaved('[{"arxiv_id":"a"},{"arxiv_id":"b","title":"B","bibtex":"@x"}]')).toEqual([
      { arxiv_id: "b", title: "B", bibtex: "@x" },
    ]);
  });

  it("writes one file with the author caveat on top", () => {
    const file = bibtexFile([paper("a"), paper("b")]);
    expect(file.startsWith("% ")).toBe(true);
    expect(file).toContain("@misc{a,}\n\n@misc{b,}");
  });

  it("fills the question box without sending", () => {
    const qs = questionsFor({ title: "ReAct: Synergizing Reasoning", primary_category: "cs.CL" });
    expect(qs[0].label).toBe("ReAct를 인용한 cs.CL 논문");
    expect(askHref(qs[0].question)).toBe("/?q=Which%20cs.CL%20papers%20cite%20ReAct%3A%20Synergizing%20Reasoning%3F");
  });

  it("knows an arXiv id when it sees one", () => {
    expect(ARXIV_ID.test("2210.03629")).toBe(true);
    expect(ARXIV_ID.test("2210.03629v3")).toBe(true);
    expect(ARXIV_ID.test("ReAct")).toBe(false);
  });
  it("reads authors and year back out of the stored BibTeX for the CSV", () => {
    // `citations.entry`가 만든 모양 그대로입니다(제목의 & 와 _ 는 escape되어 있습니다).
    const bibtex = [
      "@misc{arxiv_2210_03629,",
      "  author        = {X Y and Z},",
      "  title         = {A \& B\_c},",
      "  year          = {2022},",
      "  primaryClass  = {cs.CL},",
      "  url           = {https://arxiv.org/pdf/2210.03629}",
      "}",
    ].join("\n");
    const csv = savedCsv([{ arxiv_id: "2210.03629", title: 'A & B_c, "quoted"', bibtex }]);
    expect(csv.startsWith("﻿arxiv_id,title,authors,year,primary_category,url\n")).toBe(true);
    expect(csv).toContain('2210.03629,"A & B_c, ""quoted""",X Y; Z,2022,cs.CL,https://arxiv.org/pdf/2210.03629');
  });

  it("counts entries whose author or year is blank", () => {
    const full = "@misc{k,\n  author = {X},\n  year = {2022},\n}";
    expect(savedGaps([paper("2210.03629"), { ...paper("2401.00001"), bibtex: full }])).toEqual({ author: 1, year: 1 });
  });
});
