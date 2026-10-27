"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// 만든 화면만 올립니다. 논문·탐색·품질은 다음 PR들에서 더합니다(docs/MIGRATION_PLAN.md 4단계).
const PAGES = [{ href: "/", label: "질문" }];

export function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="주 메뉴" className="nav">
      {PAGES.map((p) => (
        <Link key={p.href} href={p.href} aria-current={path === p.href ? "page" : undefined}>
          {p.label}
        </Link>
      ))}
    </nav>
  );
}
