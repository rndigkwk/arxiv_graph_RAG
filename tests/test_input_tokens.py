"""입력 토큰 분해가 **접속 없이** 되고, 비중 계산이 맞는지 검증합니다.

[ADR-025](../docs/adr/ADR-025-reject-context-compression.md)의 기각 근거가 이 수치 하나입니다
("압축 대상이 3%"). 그러므로 분모와 분자가 섞이지 않는지, 그리고 세는 데 Aura·OpenAI 접속이
필요하지 않은지를 고정합니다 - 접속이 필요하면 CI에서 다시 셀 수 없습니다.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "measure_input_tokens", Path("scripts/measure_input_tokens.py")
)
measure = importlib.util.module_from_spec(_spec)
sys.modules["measure_input_tokens"] = measure
_spec.loader.exec_module(measure)

REPORT = Path("reports/input_tokens.json")


def test_the_schema_is_read_without_importing_chatbot():
    """`chatbot` 임포트는 Aura와 OpenAI에 붙습니다. 토큰을 세는 데 필요하면 안 됩니다."""
    source = Path("scripts/measure_input_tokens.py").read_text(encoding="utf-8")

    # 산문에 적힌 모듈 이름이 아니라 **임포트문**만 봅니다.
    assert not re.search(r"^\s*(?:import\s+chatbot|from\s+chatbot\b)", source, re.M)
    assert "chatbot" not in sys.modules

    schema = json.loads(measure.schema_json())

    assert "node_types" in schema
    assert {node["label"] for node in schema["node_types"]} == {
        "ArxivPaper", "ArxivAuthorName",
    }


def test_every_tool_description_is_found():
    """하나라도 빠지면 호출당 고정분이 작게 나와 기각 근거가 흔들립니다."""
    docs = measure.tool_docs()

    assert set(docs) == set(measure.TOOL_NAMES)
    assert all(text.strip() for text in docs.values())


def test_the_fixed_payload_is_the_system_prompt_plus_the_tool_docs():
    fixed = measure.fixed_payload(measure.encoder())

    assert fixed["per_call"] == fixed["system_prompt"] + fixed["tool_docs_total"]
    # 스키마와 Cypher 규칙은 시스템 프롬프트 **안에** 들어갑니다. 더해서 세면 이중 계산입니다.
    assert fixed["system_prompt"] > fixed["schema_json"] + fixed["cypher_rules"]


def test_the_recorded_shares_add_up():
    recorded = json.loads(REPORT.read_text(encoding="utf-8"))
    p50 = recorded["p50"]

    assert p50["rest"] == p50["total"] - p50["contexts"] - p50["hint"] - p50["question"]
    assert recorded["contexts_share"] == round(p50["contexts"] / p50["total"], 4)
    # ADR-025가 "3%"라고 적은 근거입니다. 이 값이 커지면 기각을 다시 봐야 합니다.
    assert recorded["contexts_share"] < 0.10
