"""상주 메모리를 재는 경로가 실제로 값을 돌려주는지 확인합니다.

측정 도구가 조용히 0이나 예외를 내면 "메모리가 늘지 않았다"로 잘못 읽힙니다. Windows에서는
`argtypes`를 적지 않아 64비트 핸들이 잘려 호출이 실패한 적이 있습니다.
"""

import sys

from scripts.measure_app_memory import BASELINE_STAGES, STREAMLIT_GUARANTEED_MB, resident_mb


def test_resident_memory_is_a_positive_number():
    value = resident_mb()

    assert isinstance(value, float)
    # 인터프리터만으로도 수 MB는 씁니다. 0이면 측정이 실패한 것입니다.
    assert value > 1.0


def test_resident_memory_grows_when_memory_is_held():
    before = resident_mb()
    # 약 40MB. 할당만 하고 참조를 유지해야 상주에 남습니다.
    held = bytearray(40 * 1024 * 1024)
    held[::4096] = b"x" * (len(held) // 4096)
    after = resident_mb()

    assert after > before, (before, after)
    del held


def test_the_recorded_baseline_is_the_one_the_decision_used():
    """ADR-016이 쓴 수치와 같아야 비교가 성립합니다."""
    assert BASELINE_STAGES["vector_search"] == 259
    assert STREAMLIT_GUARANTEED_MB == 690


def test_the_platform_branch_exists_for_this_machine():
    # 다른 플랫폼에서는 /proc 을 읽습니다. 둘 중 하나는 반드시 동작해야 합니다.
    assert sys.platform == "win32" or sys.platform.startswith("linux")
