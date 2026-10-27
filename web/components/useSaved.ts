"use client";

import { useCallback, useEffect, useState } from "react";
import { type Saved, parseSaved, toggleSaved } from "@/lib/paper";

const KEY = "arxiv-graphrag.saved";
const EVENT = "saved-changed";

function read(): Saved[] {
  try {
    return parseSaved(localStorage.getItem(KEY));
  } catch {
    return []; // 사생활 보호 창 등에서 저장소가 막혀 있어도 화면은 뜹니다.
  }
}

// 저장 목록은 이 브라우저에만 둡니다(계정이 없습니다). 같은 탭의 다른 컴포넌트에도 알립니다.
export function useSaved() {
  const [saved, setSaved] = useState<Saved[]>([]);
  useEffect(() => {
    const sync = () => setSaved(read());
    sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const toggle = useCallback((paper: Saved) => {
    const next = toggleSaved(read(), paper);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {}
    setSaved(next);
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return { saved, toggle };
}
