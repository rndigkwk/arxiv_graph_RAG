"""되읽기 프로브가 지켜야 할 것들입니다(ADR-062).

이 파일이 막는 것은 하나입니다 — **고장 난 검출기가 "저장되지 않음"처럼 보이는 것.**
2026-10-23에 그랬습니다: `api.trace.get`을 불러 `AttributeError`를 60번 내고 그것을 "저장되지
않음"으로 보고했습니다. 한도가 닫힌 달에 만들어서 음성 대조군이 잡았고, 11월에 만들었다면
한도 탓으로 읽었을 것입니다.
"""

import importlib.util
import json
from pathlib import Path

import pytest

PROBE = Path("scripts/probe_langfuse_delivery.py")
ADR = Path("docs/adr/ADR-062-read-back-whether-langfuse-stored-it.md")
REPORT = Path("reports/langfuse_delivery.json")


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePage:
    def __init__(self, data):
        self.data = data


class FakeObservations:
    """`get_many`만 흉내 냅니다 - SDK가 쓰라고 적어 둔 그 메서드입니다."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def get_many(self, trace_id):
        self.calls += 1
        value = self.pages[min(self.calls - 1, len(self.pages) - 1)]
        if isinstance(value, Exception):
            raise value
        return FakePage(value)


class FakeClient:
    def __init__(self, pages):
        self.api = type("Api", (), {"observations": FakeObservations(pages)})()


# ── 음성 대조군이 잡은 결함 둘 ──────────────────────────────────────────

def test_it_reads_back_with_the_method_the_sdk_documents(probe):
    """`api.trace`에는 읽기 메서드가 없습니다(delete·delete_multiple뿐)."""
    source = PROBE.read_text(encoding="utf-8")
    assert "api.observations.get_many(trace_id=" in source
    assert "api.trace.get(" not in source


def test_a_broken_call_is_reported_not_retried(probe):
    """프로그래밍 오류를 60번 되묻는 동안 "저장되지 않음"처럼 보이는 것이 더 나쁩니다."""
    client = FakeClient([AttributeError("no attribute 'get'")])
    out = probe.read_back(client, "abc", wait=30, poll=0)

    assert out["found"] is False
    assert "AttributeError" in out["error"]
    # 한 번에 멈춥니다.
    assert out["attempts"] == 1
    assert client.api.observations.calls == 1
    # **저장 여부를 말하지 않는다고 적어야 합니다.**
    assert "저장 여부를 말하지 않습니다" in out["means"]


def test_not_yet_visible_is_retried_because_ingestion_is_async(probe):
    """SDK가 "보통 15~30초, 부하가 크면 더"라고 적었습니다. 한 번 보고 없다고 하면 안 됩니다."""
    client = FakeClient([[], [], [{"id": "o1"}]])
    out = probe.read_back(client, "abc", wait=30, poll=0)

    assert out["found"] is True
    assert out["observations"] == 1
    assert out["attempts"] == 3


def test_a_never_sent_id_is_not_found(probe):
    """검출기가 아무 id에나 "찾았다"고 하면 결과가 뜻을 잃습니다."""
    client = FakeClient([[]])
    out = probe.read_back(client, "never-sent", wait=0.2, poll=0.1)

    assert out["found"] is False
    assert "구별되지 않습니다" in out["means"]


# ── 설계 ────────────────────────────────────────────────────────────────

def test_it_is_not_named_like_a_checker():
    """CI가 `scripts/check_*.py`를 전부 돌립니다. 키가 필요한 전송이 거기 들어가면 안 됩니다."""
    assert PROBE.name.startswith("probe_")
    assert not list(Path("scripts").glob("check_langfuse*"))


def test_it_does_not_spend_the_daily_budget():
    """예산이 소진된 날에도 진단은 돌아야 합니다. 그 사실을 보고서에 적습니다."""
    source = PROBE.read_text(encoding="utf-8")
    assert "send_trace" not in source
    assert "_record_sent" not in source
    assert "LANGFUSE_MAX_TRACES" in source  # 쓰지 않는다는 설명으로만


def test_the_probe_sends_no_content_beyond_a_name_and_a_time():
    """ADR-023 3번을 진단용 trace에도 지킵니다.

    **주석은 벗기고 봅니다.** 금지어를 설명하는 주석 자체가 걸립니다 - 이 저장소에서 **세
    번째**입니다(PR #22의 `continue-on-error`, 노트북의 `SystemExit(0)`, 그리고 여기).
    """
    source = PROBE.read_text(encoding="utf-8")
    body = source[source.index("def send("): source.index("def read_back(")]
    code = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    ).lower()
    for forbidden in ("question", "answer", "cypher", "rows"):
        assert forbidden not in code, forbidden
    assert '"sent_at"' in body


def test_the_keys_are_never_printed():
    """로컬 .env에만 두고 GitHub Secrets에는 올리지 않습니다(ADR-023)."""
    source = PROBE.read_text(encoding="utf-8")
    assert "값은 출력하지 않습니다" in source
    assert "print(keys" not in source
    assert "secret_key]" not in source.replace('os.environ["LANGFUSE_SECRET_KEY"]', "")


# ── 측정 결과 ───────────────────────────────────────────────────────────

def test_the_recorded_run_found_the_trace():
    if not REPORT.exists():
        pytest.skip(f"보고서를 읽을 수 없습니다: {REPORT}")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["auth_check"] is True
    assert report["read_back"]["found"] is True
    assert report["verdict"] == "저장됩니다"


def test_the_adr_records_that_the_bar_predicted_the_opposite():
    """예측이 틀린 자리를 지우면 "그럴 줄 알았다"가 됩니다."""
    text = ADR.read_text(encoding="utf-8")
    assert "바가 예측한 것과 반대입니다" in text
    # 그리고 ADR-023의 낡은 문장이 뒤집힘으로 표시돼 있어야 합니다.
    other = Path("docs/adr/ADR-023-what-to-trace.md").read_text(encoding="utf-8")
    assert "이 문장이 뒤집혔습니다" in other
