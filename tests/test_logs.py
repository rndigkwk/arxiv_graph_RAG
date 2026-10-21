"""운영 로그 설정이 **한 번만, 우리 로거에만, 데이터 없이** 붙는지 봅니다.

세 가지가 무너지면 로그가 쓸모없어집니다.

| 무너지면 | 결과 |
|---|---|
| 두 번 붙는다 | Streamlit은 상호작용마다 스크립트를 다시 돌립니다. 줄이 재실행 수만큼 복제됩니다 |
| 루트에 붙는다 | Streamlit·neo4j 드라이버의 줄까지 형식이 바뀝니다. 우리가 정할 일이 아닙니다 |
| 수준 오타가 DEBUG로 읽힌다 | 운영 로그가 데이터로 찹니다 |

`configure()`가 **무엇을 했는지 돌려주는** 것도 여기서 고정합니다. 조용히 설정하고 아무 말도
하지 않으면 안 붙은 것과 구별되지 않습니다.
"""

import ast
import io
import logging
from pathlib import Path

import pytest

from pipeline.logs import (
    DEFAULT_LEVEL,
    FILE_KEY,
    LEVEL_KEY,
    LOGGER_NAME,
    MARK,
    configure,
    describe,
    level,
    ours,
)


@pytest.fixture(autouse=True)
def clean_logger():
    """테스트마다 우리 로거를 원래대로 돌려놓습니다. 모듈 전역 상태이므로 새지 않게."""
    logger = logging.getLogger(LOGGER_NAME)
    before = (list(logger.handlers), logger.level, logger.propagate)
    logger.handlers = []
    yield logger
    for handler in logger.handlers:
        handler.close()
    logger.handlers, logger.level, logger.propagate = before


# ── 수준 ────────────────────────────────────────────────────────────────────

def test_the_default_level_is_warning():
    assert level({}) == getattr(logging, DEFAULT_LEVEL)


def test_a_level_is_read_case_insensitively():
    assert level({LEVEL_KEY: "info"}) == logging.INFO
    assert level({LEVEL_KEY: "  DEBUG  "}) == logging.DEBUG


def test_a_typo_falls_back_to_the_default_not_to_debug():
    """`WARINNG`가 DEBUG로 읽혀 운영 로그를 데이터로 채우면 안 됩니다(ADR-023과 같은 방향)."""
    for value in ("WARINNG", "verbose", "10", "", "   ", "TRACE"):
        assert level({LEVEL_KEY: value}) == getattr(logging, DEFAULT_LEVEL), value


# ── 어디에 붙는가 ──────────────────────────────────────────────────────────

def test_it_attaches_to_our_logger_and_leaves_the_root_alone(clean_logger):
    root_before = list(logging.getLogger().handlers)

    state = configure({}, stream=io.StringIO())

    assert state["logger"] == LOGGER_NAME
    assert len(ours(clean_logger)) == 1
    assert logging.getLogger().handlers == root_before


def test_it_does_not_propagate(clean_logger):
    """Streamlit이 루트를 설정합니다. 올려보내면 같은 줄이 두 번 찍힙니다."""
    configure({}, stream=io.StringIO())

    assert clean_logger.propagate is False


def test_a_second_call_adds_nothing(clean_logger):
    """**Streamlit 재실행마다 줄이 복제되는 것을 막는 테스트입니다.**"""
    first = configure({}, stream=io.StringIO())
    second = configure({}, stream=io.StringIO())

    assert first["already_configured"] is False
    assert second["already_configured"] is True
    assert len(ours(clean_logger)) == 1


def test_our_handlers_are_marked(clean_logger):
    """표식이 없으면 "이미 붙었나"를 알 수 없어 두 번째 호출이 복제합니다.

    전체 핸들러가 전부 우리 것이라고 단정하지 **않습니다** - pytest가 `propagate=False`인
    로거에 자기 포착 핸들러를 직접 붙입니다. 우리 것이 표식을 갖는지만 봅니다.
    """
    state = configure({}, stream=io.StringIO())

    assert len(ours(clean_logger)) == state["handlers"] == 1
    assert all(getattr(h, MARK, False) for h in ours(clean_logger))


def test_other_handlers_on_our_logger_do_not_break_the_check(clean_logger):
    """**pytest가 이 상황을 실제로 만듭니다.**

    `propagate=False`인 로거를 보면 pytest가 자기 포착 핸들러를 **직접** 붙입니다
    (`LogCaptureHandler`, `_FileHandler`, `_LiveLoggingNullHandler`). 전체 개수로
    "이미 설정됐나"를 판단하면 그 환경에서 거짓이 되고, 두 번째 호출이 복제하거나
    첫 호출이 건너뜁니다. 이 테스트가 그 자리를 고정합니다.
    """
    stranger = logging.NullHandler()
    clean_logger.addHandler(stranger)

    first = configure({}, stream=io.StringIO())
    second = configure({}, stream=io.StringIO())

    assert first["already_configured"] is False
    assert second["already_configured"] is True
    assert len(ours(clean_logger)) == 1
    # 남의 핸들러는 건드리지 않습니다.
    assert stranger in clean_logger.handlers


def test_importing_the_app_does_not_configure_logging():
    """**임포트에 부작용을 두지 않습니다**(ADR-053).

    처음에 `app.py` 최상단에서 `configure()`를 불렀고, `app`을 임포트하는 테스트 두 개가
    그 부작용을 받아 전역 로깅이 바뀌었습니다. Streamlit은 스크립트를 `__main__`으로
    돌리므로 `main()` 안이 그 자리입니다.
    """
    source = Path("app.py").read_text(encoding="utf-8")
    body = source[source.index("def main():"):]

    assert "logs.configure()" in body
    # 모듈 최상단에는 없어야 합니다.
    assert "logs.configure()" not in source[:source.index("def main():")]


# ── 줄의 모양 ──────────────────────────────────────────────────────────────

def test_a_line_carries_the_time_the_level_and_the_logger_name(clean_logger):
    """**없던 것이 바로 이 셋입니다.** `lastResort`는 메시지만 찍습니다."""
    buffer = io.StringIO()
    configure({}, stream=buffer)

    logging.getLogger(f"{LOGGER_NAME}.graph").warning(
        "graph_load_retry attempt=%d reason=%s seconds=%.1f", 1, "ServiceUnavailable", 60.1
    )
    line = buffer.getvalue().strip()

    assert "WARNING" in line
    assert f"{LOGGER_NAME}.graph" in line
    assert "seconds=60.1" in line
    # ISO 날짜가 앞에 옵니다. 시각이 없으면 "언제, 얼마나 자주"를 셀 수 없습니다.
    assert line[:4].isdigit() and line[4] == "-" and "T" in line[:20]


def test_the_timestamp_is_utc(clean_logger):
    """두 기계의 줄을 나란히 읽어야 하고 Aura 콘솔도 UTC입니다."""
    import time as time_module

    buffer = io.StringIO()
    configure({}, stream=buffer)
    logging.getLogger(f"{LOGGER_NAME}.graph").warning("probe")

    stamped = buffer.getvalue().split()[0]
    assert stamped.startswith(time_module.strftime("%Y-%m-%dT%H", time_module.gmtime()))


def test_a_quiet_level_filters_the_line_out(clean_logger):
    buffer = io.StringIO()
    configure({LEVEL_KEY: "ERROR"}, stream=buffer)

    logging.getLogger(f"{LOGGER_NAME}.graph").warning("graph_load_retry attempt=1")

    assert buffer.getvalue() == ""


# ── 파일 ────────────────────────────────────────────────────────────────────

def test_a_file_is_added_when_asked(clean_logger, tmp_path):
    path = tmp_path / "logs" / "app.log"

    state = configure({FILE_KEY: str(path)}, stream=io.StringIO())
    logging.getLogger(f"{LOGGER_NAME}.validation").warning("answer_validation_failed reason=x")

    assert state["file"] == str(path)
    assert state["file_error"] is None
    assert len(ours(clean_logger)) == 2
    assert "answer_validation_failed" in path.read_text(encoding="utf-8")


def test_an_unwritable_file_does_not_stop_the_app(clean_logger, tmp_path):
    """**로그를 못 쓰는 것이 앱을 멈출 이유는 아닙니다.** 다만 조용히 묻지도 않습니다."""
    blocked = tmp_path / "taken"
    blocked.mkdir()

    state = configure({FILE_KEY: str(blocked)}, stream=io.StringIO())

    assert state["file"] is None
    assert state["file_error"]
    # stderr 쪽은 그대로 살아 있어야 합니다.
    assert state["stream"] is True
    assert len(ours(clean_logger)) == 1


def test_no_file_is_added_by_default(clean_logger):
    state = configure({}, stream=io.StringIO())

    assert state["file"] is None
    assert len(ours(clean_logger)) == 1


# ── 무엇을 했는지 말한다 ───────────────────────────────────────────────────

def test_it_says_where_the_lines_go(clean_logger, tmp_path):
    plain = describe(configure({}, stream=io.StringIO()))
    assert plain == "로그 WARNING -> stderr"

    logging.getLogger(LOGGER_NAME).handlers = []
    withfile = describe(
        configure({FILE_KEY: str(tmp_path / "a.log"), LEVEL_KEY: "INFO"}, stream=io.StringIO())
    )
    assert withfile.startswith("로그 INFO -> stderr + ")


# ── 데이터를 담지 않는다 ───────────────────────────────────────────────────

def log_call_arguments(path: str) -> list[tuple[str, list[str]]]:
    """`*_logger.warning(...)` 호출의 **인자**를 꺼냅니다. 임포트하지 않고 파싱만 합니다."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "warning"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id.endswith("_logger")):
            continue
        first = node.args[0] if node.args else None
        template = first.value if isinstance(first, ast.Constant) else ""
        found.append((template, [ast.unparse(arg) for arg in node.args[1:]]))
    return found


# 로그에 넘겨도 되는 값. **데이터가 아니라 메타데이터만** 있습니다. 질문·답변·Cypher·행을
# 넘기려면 이 목록을 늘려야 하고, 그러려면 ADR을 고쳐야 합니다(ADR-023 결정 3).
SAFE_ARGUMENTS = frozenset({
    "request_id", "attempt", "attempt + 1", "stage", "reason",
    "elapsed", "type(exc).__name__",
})


def test_neither_call_site_passes_anything_but_metadata():
    """기존 선과 같습니다 - 운영 로그에 데이터를 남기지 않습니다(ADR-023 결정 3).

    단어로 세지 않고 **인자**를 봅니다. `answer_validation_failed`는 이벤트 이름이지
    데이터가 아니므로, 서식 문자열에 "answer"가 있다고 막으면 안 됩니다. 막아야 하는 것은
    질문 문장·답변 본문·Cypher·조회한 행이 **값으로** 넘어가는 것입니다.
    """
    calls = log_call_arguments("answer_validation.py") + log_call_arguments("chatbot.py")

    assert len(calls) == 3, [template for template, _ in calls]
    for template, arguments in calls:
        for argument in arguments:
            assert argument in SAFE_ARGUMENTS, f"{template[:40]} -> {argument}"


def test_the_templates_name_the_event_and_the_fields():
    """`키=값`으로 적어야 나중에 세고 가를 수 있습니다. 산문으로 적으면 못 합니다."""
    calls = log_call_arguments("answer_validation.py") + log_call_arguments("chatbot.py")
    events = {template.split()[0] for template, _ in calls}

    assert events == {
        "answer_validation_failed", "graph_load_retry", "graph_load_failed"
    }
    for template, arguments in calls:
        # 자리표시자 수와 인자 수가 맞아야 합니다. 어긋나면 운영에서 포맷 오류가 납니다.
        assert template.count("%") == len(arguments), template
