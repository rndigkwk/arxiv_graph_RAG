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
