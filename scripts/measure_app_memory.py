"""앱의 상주 메모리를 단계별로 재고, 선택한 패키지가 얼마를 더하는지 봅니다.

    uv run python scripts/measure_app_memory.py
    uv run --with langfuse python scripts/measure_app_memory.py --import langfuse.langchain

`ROADMAP.md` 8-1-1절이 2026-10-14에 같은 방식으로 재서 **259MB**를 얻었고, 그 값이
[ADR-016](../docs/adr/ADR-016-stay-on-streamlit.md)의 "Cloud Run으로 옮기지 않는다"와
Streamlit 보장치 690MB 안에 있다는 판단의 근거입니다. 앱 의존성을 더할 때마다 그 수치가
무효가 되므로, 더하기 전에 다시 재는 스크립트를 둡니다.

**설치가 아니라 임포트가 메모리를 씁니다.** 그래서 `--import`로 **앱이 실제로 가져올 모듈**을
지정합니다. `uv run --with <패키지>`로 돌리면 `pyproject.toml`과 잠금 파일을 건드리지 않고
그 실행에만 설치되므로, 재 보기 전에 앱 의존성을 늘리지 않습니다.

psutil을 쓰지 않습니다. 측정 때문에 의존성을 더하면 재려는 값이 바뀝니다. Windows에서는
`GetProcessMemoryInfo`, 그 밖에서는 `/proc/self/statm`을 직접 읽습니다.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# `scripts/`에서 돌리므로 저장소 최상위를 경로에 넣어야 `chatbot`을 가져올 수 있습니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUTPUT = Path("reports/app_memory.json")
# 2026-10-14 측정(ROADMAP 8-1-1절). 같은 단계로 재서 비교합니다.
BASELINE_STAGES = {
    "interpreter": 19,
    "chatbot_imported": 212,
    "citation_graph_loaded": 257,
    "global_pagerank": 257,
    "vector_search": 259,
    "personalized_pagerank": 259,
}
STREAMLIT_GUARANTEED_MB = 690


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def resident_mb() -> float:
    """Resident set size in MB, without adding a dependency to measure it."""
    if sys.platform == "win32":
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # argtypes를 적지 않으면 64비트 핸들이 int로 잘려 호출이 실패합니다.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        query = kernel32.K32GetProcessMemoryInfo
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Counters), ctypes.c_ulong]
        query.restype = ctypes.c_int
        if not query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "K32GetProcessMemoryInfo 실패")
        return round(counters.WorkingSetSize / 1024 / 1024, 1)
    pages, resident = Path("/proc/self/statm").read_text().split()[:2]
    return round(int(resident) * 4096 / 1024 / 1024, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="앱 상주 메모리를 단계별로 측정합니다.")
    parser.add_argument("--import", dest="modules", nargs="*", default=[],
                        help="앱이 실제로 가져올 모듈. 임포트 전후를 따로 적습니다.")
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path, default=OUTPUT,
                        help="이미 있으면 --overwrite 없이는 쓰지 않습니다.")
    parser.add_argument("--overwrite", action="store_true",
                        help="--output이 이미 있어도 덮어씁니다.")
    parser.add_argument("--skip-app", action="store_true",
                        help="chatbot을 가져오지 않고 패키지 비용만 봅니다(네트워크 없이).")
    args = parser.parse_args()
    # **재기 전에 거부합니다.** 2026-10-25에 기본값이 10-18 보고서를 조용히 덮었고, 그 보고서에
    # 묶인 "Langfuse +5.2MB"가 다른 실행끼리 뺀 수가 됐습니다(EXPERIMENTS 11-49절). 다 잰 뒤에
    # 거부하면 측정 시간을 버리므로 앞에서 막습니다.
    if args.output.exists() and not args.overwrite:
        parser.error(f"{args.output}이 이미 있습니다. 다른 실행의 보고서를 덮지 않도록 "
                     "--output으로 새 이름을 주거나, 덮으려면 --overwrite를 주세요.")

    stages: dict[str, float] = {"interpreter": resident_mb()}

    extra: dict[str, Any] = {}
    for name in args.modules:
        before = resident_mb()
        __import__(name)
        after = resident_mb()
        extra[name] = {"before": before, "after": after, "delta": round(after - before, 1)}
        stages[f"imported:{name}"] = after

    if not args.skip_app:
        from src import chatbot

        stages["chatbot_imported"] = resident_mb()
        chatbot.citation_graph()
        stages["citation_graph_loaded"] = resident_mb()
        chatbot.global_pagerank()
        stages["global_pagerank"] = resident_mb()
        chatbot.search_papers.invoke({"query": "graph neural network", "top_k": 5})
        stages["vector_search"] = resident_mb()

    peak = max(stages.values())
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "imported": list(args.modules),
        "skipped_app": args.skip_app,
        "stages_mb": stages,
        "peak_mb": peak,
        "extra_imports": extra,
        "baseline_2026_10_14_mb": BASELINE_STAGES,
        "streamlit_guaranteed_mb": STREAMLIT_GUARANTEED_MB,
        "headroom_mb": round(STREAMLIT_GUARANTEED_MB - peak, 1),
        "note": (
            "설치가 아니라 임포트가 메모리를 씁니다. `uv run --with <패키지>`로 돌리면 "
            "pyproject와 잠금 파일을 건드리지 않습니다. ROADMAP 8-1-1절과 같은 단계로 재서 "
            "비교합니다."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {key: report[key] for key in ("stages_mb", "peak_mb", "headroom_mb", "extra_imports")},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
