// 논문 페이지와 저장 목록의 순수 부분. 화면과 나눠 두면 브라우저 없이 테스트합니다.

import type { Paper } from "./api";

export const ARXIV_ID = /^\d{4}\.\d{4,5}(v\d+)?$/;
export const MAX_SAVED = 20; // Streamlit 저장 목록과 같은 한도입니다.

export type Saved = { arxiv_id: string; title: string; bibtex: string };

// 질문 작업대로 넘기는 질문입니다. 보내지는 않고 입력칸만 채웁니다(누르면 그때 돈이 듭니다).
export function questionsFor(paper: Pick<Paper, "title" | "primary_category">): { label: string; question: string }[] {
  const short = paper.title.split(":")[0].trim();
  return [
    { label: `${short}를 인용한 ${paper.primary_category} 논문`, question: `Which ${paper.primary_category} papers cite ${paper.title}?` },
    { label: `${short} 저자들의 다른 논문`, question: `What other papers did the authors of ${paper.title} write?` },
    { label: `${short}와 관련된 논문`, question: `Which papers are related to ${paper.title}?` },
  ];
}

export const authorQuestion = (name: string) => `Which papers did ${name} write?`;
export const askHref = (question: string) => `/?q=${encodeURIComponent(question)}`;

// 저장 목록은 이 브라우저에만 남습니다. 깨진 값은 빈 목록으로 읽습니다.
export function parseSaved(raw: string | null): Saved[] {
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value)
      ? value.filter((s) => s && typeof s.arxiv_id === "string" && typeof s.bibtex === "string")
      : [];
  } catch {
    return [];
  }
}

export function toggleSaved(list: Saved[], paper: Saved): Saved[] {
  if (list.some((s) => s.arxiv_id === paper.arxiv_id)) return list.filter((s) => s.arxiv_id !== paper.arxiv_id);
  if (list.length >= MAX_SAVED) return list;
  return [...list, { arxiv_id: paper.arxiv_id, title: paper.title, bibtex: paper.bibtex }];
}

// 저자 이름은 동명이인을 가리지 않은 표기입니다 - 그 사실을 파일 머리에 적습니다(ADR-042).
export function bibtexFile(list: Saved[]): string {
  const head = "% arXiv GraphRAG 저장 목록. 저자 이름은 동명이인을 구별하지 않은 표기입니다.\n\n";
  return head + list.map((s) => s.bibtex.trim()).join("\n\n") + "\n";
}

export const year = (published: string | null) => (published ? published.slice(0, 10) : "");

// BibTeX 필드 하나. 저장 목록에는 BibTeX만 남아 있어서, CSV의 저자·연도도 여기서 읽습니다.
function field(bibtex: string, name: string): string {
  const match = bibtex.match(new RegExp(String.raw`^\s*${name}\s*=\s*\{(.*)\},?\s*$`, "m"));
  if (!match) return "";
  // `citations.escape`를 거꾸로 합니다. 제목의 `\&`가 CSV에 그대로 나오면 안 됩니다.
  return match[1]
    .replace(/\\textbackslash\{\}/g, "\\")
    .replace(/\\textasciitilde\{\}/g, "~")
    .replace(/\\textasciicircum\{\}/g, "^")
    .replace(/\\([{}&%$#_])/g, "$1");
}

export type SavedRow = { arxiv_id: string; title: string; authors: string; year: string; primary_category: string; url: string };

export function savedRows(list: Saved[]): SavedRow[] {
  return list.map((s) => ({
    arxiv_id: s.arxiv_id,
    title: s.title,
    authors: field(s.bibtex, "author").split(" and ").filter(Boolean).join("; "),
    year: field(s.bibtex, "year"),
    primary_category: field(s.bibtex, "primaryClass"),
    url: field(s.bibtex, "url"),
  }));
}

const cell = (text: string) => (/[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text);

// Excel이 BOM 없이는 한글 제목을 깨뜨립니다(Streamlit도 utf-8-sig로 내려보냈습니다).
export function savedCsv(list: Saved[]): string {
  const head: (keyof SavedRow)[] = ["arxiv_id", "title", "authors", "year", "primary_category", "url"];
  const rows = savedRows(list).map((r) => head.map((k) => cell(r[k])).join(","));
  return "﻿" + [head.join(","), ...rows].join("\n") + "\n";
}

/** 저자·연도가 빈 항목 수. 보이지 않으면 불완전한 파일을 완전한 줄 알고 씁니다(ADR-042). */
export function savedGaps(list: Saved[]): { author: number; year: number } {
  const rows = savedRows(list);
  return { author: rows.filter((r) => !r.authors).length, year: rows.filter((r) => !r.year).length };
}
