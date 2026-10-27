"""실행: uv run streamlit run app.py --server.fileWatcherType none"""
import json
import re
from datetime import datetime
from textwrap import fill
from urllib.parse import quote

import streamlit as st

from pipeline import logs
from src import (
    abstain,
    citations,
    family_board,
    model_outage,
    quality_board,
    release,
    routing,
    source_check,
    warmup,
)
from src.paper_library import add_paper, compare_selection


# 연결·검색기는 공유하고, 각 사용자의 대화는 아래 session_state에 따로 저장합니다.
@st.cache_resource
def load_chatbot():
    """화면을 다시 그려도 연결과 검색 저장소를 재사용합니다."""
    # 준비 에러도 main에서 안내할 수 있게 여기서 검색 모듈을 불러옵니다.
    from src import chatbot

    return chatbot


@st.cache_resource
def citation_warmup(_chatbot):
    """인용망을 배경에서 미리 올립니다. **프로세스당 한 번만** 시작합니다.

    `_chatbot`에 밑줄이 붙은 것은 Streamlit에게 **해시하지 말라**고 말하는 표시입니다 -
    모듈 객체는 해시할 수 없고, 어차피 이 프로세스에 하나뿐입니다.

    배경 스레드에 `load_chatbot()`을 넘기지 않고 **이미 불러온 모듈**을 받는 이유는, 그
    함수가 Streamlit 캐시라서 스크립트 스레드 밖에서 부르면 컨텍스트 경고가 나기
    때문입니다. 무거운 일(`citation_graph`)만 배경으로 보냅니다.
    """
    task = warmup.Warmup(_chatbot.citation_graph)
    task.start()
    return task


@st.cache_data(show_spinner=False)
def translate(text):
    """같은 답변을 여러 번 눌러도 한 번만 번역합니다."""
    return load_chatbot().translate_to_korean(text)


@st.cache_data(show_spinner=False)
def graph_counts():
    """홈 화면 메타그래프에 쓸 노드·관계 수입니다."""
    return load_chatbot().graph_counts()


# 세 칸 작업대의 너비 비율입니다 — 왼쪽 경로, 가운데 답변, 오른쪽 근거(ADR-076). 화면이 좁으면
# Streamlit이 칸을 세로로 쌓습니다.
WORKBENCH = (1, 2.2, 1.4)

# 화면은 다시 그릴 때마다 대화 기록의 답변을 **전부** 다시 그리고, 답변마다 그래프를 읽습니다.
# 한 번에 Aura 질의 두 개, 측정 중앙값 0.40초라 답변이 다섯 개면 버튼 하나에 2초가 듭니다
# (ADR-075). 그래프는 주간 수집 때만 바뀌므로 6시간을 둡니다. 실패는 캐시하지 않습니다.
SUBGRAPH_TTL_SECONDS = 6 * 3600


@st.cache_data(show_spinner=False, ttl=SUBGRAPH_TTL_SECONDS, max_entries=256)
def subgraph_of(ids):
    """같은 논문 묶음의 저자·인용 관계는 한 번만 읽습니다. `ids`는 튜플이어야 해시됩니다."""
    return load_chatbot().fetch_subgraph(list(ids))


@st.cache_data(show_spinner=False)
def quality_report():
    """품질 리포트 노트북이 저장한 JSON입니다. 없으면 None."""
    from src import metagraph

    return metagraph.load_report()


@st.cache_data(show_spinner=False)
def ranking(code, limit, include_secondary, scope):
    """같은 조건의 PageRank 순위는 한 번만 계산합니다."""
    return load_chatbot().category_pagerank(code, limit, include_secondary, scope)


# 점수가 1e-4 수준이라 기본 표시로는 전부 0.0000이 됩니다. 지수 표기로 보여 줍니다.
SCORE_COLUMN = {"PageRank": st.column_config.NumberColumn("PageRank", format="%.3e")}

# `rank_related_papers`는 공동인용으로 뽑고, 부족하면 개인화 PageRank, 그다음 임베딩으로
# 채웁니다. 논문마다 `ranked_by`가 어느 쪽인지 말해 주는데(chatbot.py) **화면이 그것을 읽지
# 않았습니다.** 그래서 캡션은 "개인화 PageRank: 선택됨"이라고만 적었고(주 경로는 공동인용입니다
# - ADR-034), 임베딩이 채운 논문은 "벡터: 선택 안 됨"인 채로 그래프에 그려졌습니다.
MECHANISM_LABEL = {
    "cocitation": "공동인용", "pagerank": "개인화 PageRank",
    "embedding": "임베딩 보충", "unknown": "분류 없음",
}
# 점수의 **단위가 메커니즘마다 다릅니다** - 공동인용은 공유 인용자 수(정수), PageRank는 확률,
# 임베딩이 채운 논문은 둘 다 없어서 0.0입니다. 한 열에 "PageRank"라고 적으면 거짓이 됩니다.
MECHANISM_UNIT = {
    "cocitation": "공유 인용자 수", "pagerank": "PPR 점수",
    "embedding": "점수 없음 (0.0)", "unknown": "",
}
# 근거로 인용된 논문과 후보를 테두리로 가릅니다. 후보를 지우지 않는 이유는 공동인용 결과가 이
# 프로젝트가 재서 이긴 것이기 때문입니다(ADR-034, PPR의 2.2배).
MECHANISM_NODE_STYLE = {"cocitation": "dashed", "pagerank": "dashed", "embedding": "dotted"}
# 공유 인용자 수(정수)와 PPR 점수(확률)가 한 열에 섞이므로 지수 표기를 쓰지 않습니다.
RELATED_COLUMN = {"점수": st.column_config.NumberColumn("점수", format="%.4f")}

# StateGraph의 노드 이름(agent_graph.py)을 화면 말로 옮깁니다.
NODE_LABEL = {
    "translate": "한국어→영어 번역", "route": "경로 판정", "generate": "답변 생성",
    "collect": "결과 수집",
    "check_format": "형식·인용 검사", "check_conditions": "조건 검사",
    "revise": "한 번 수정", "accept": "통과", "abstain": "보류",
}
# `generate` 안에 **번역·Text2Cypher·벡터 검색·모델 턴이 모두** 들어 있습니다. ADR-065까지는
# 그 안을 가를 수 없어 이 말을 적었습니다 - 적지 않으면 "답변 생성이 느리다"로 읽히는데
# 사실은 "그 안의 무엇이 느린지 모른다"였기 때문입니다. ADR-066이 안쪽을 재므로 이제는
# **계측이 없을 때의 말**입니다(옛 보고서, 콜백이 걸리지 않은 실행).
GENERATE_CONTAINS = "번역·Cypher·벡터·모델 턴 포함"
# 안쪽을 잰 뒤의 말입니다. 모델 턴 하나의 안쪽(대기 대 생성)은 여전히 가를 수 없습니다.
MODEL_TURN_OPAQUE = "모델 턴 하나의 안쪽은 재지 못합니다"
TOOL_LABEL = {"translate_to_english": "한국어→영어 번역",
              "search_graph": "Text2Cypher 관계 조회",
              "search_papers": "벡터 의미 검색",
              "resolve_paper": "제목으로 논문 특정",
              # 주 경로는 공동인용입니다(ADR-034·ADR-065). 보조 경로 이름으로 부르지 않습니다.
              "rank_related_papers": "관련 논문 (공동인용·PageRank·임베딩)"}
# 11-46절이 기준선 90건에서 잰 값입니다. 이번 한 건만 보면 빠른지 느린지 알 수 없습니다.
BASELINE_P50_SECONDS = 15.5
BASELINE_MAX_SECONDS = 42.5


def add_node(statements, node_id, label, shape="box", style="solid"):
    """이름의 따옴표와 줄바꿈을 보존해 노드 선언을 추가합니다."""
    # ID는 같은 개체를 연결하는 키이고 label은 사람이 읽는 이름입니다.
    statements.add(f'{json.dumps(node_id)} [label={json.dumps(label, ensure_ascii=False)}, '
                   f'shape={shape}, style={style}];')


def add_edge(statements, start, end, label, style="solid"):
    """동일한 연결은 한 번만 넣습니다."""
    statements.add(f'{json.dumps(start)} -> {json.dumps(end)} '
                   f'[label={json.dumps(label, ensure_ascii=False)}, style={style}];')


ARXIV_ID = re.compile(r"\b\d{4}\.\d{4,5}\b")


def graph_ids(response):
    """그릴 논문 ID를 고릅니다. 인용한 근거가 없으면 이번에 검색한 논문을 보여 줍니다."""
    ids = list(response["evidence_ids"])
    # "이 논문을 인용한 논문"처럼 Cypher에 적힌 기준 논문도 넣어야 인용 화살표가 보입니다.
    ids += ARXIV_ID.findall(response["cypher"])
    # 개인화 PageRank가 찾은 이웃은 시드와 인용으로 이어져 있어 그림에서 특히 잘 보입니다.
    ids += [hit["arxiv_id"] for hit in response.get("related", [])]
    if not response["evidence_ids"]:
        ids += [pid for row in response["rows"] for pid in row.get("evidence_ids", [])]
        ids += [hit["arxiv_id"] for hit in response["papers"]]
    return list(dict.fromkeys(ids))


# 논문이 많으면 저자까지 그릴 때 가로로 늘어져 글자를 읽을 수 없습니다.
AUTHOR_LIMIT = 6


def found_by(response):
    """관련 논문을 **어떤 메커니즘이** 올렸는지 셉니다. 값은 `ranked_by`에 이미 있습니다."""
    counts = {}
    for hit in response.get("related", []):
        key = hit.get("ranked_by") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def mechanism_of(response):
    """arxiv_id → 그 논문을 올린 메커니즘. 근거로 인용된 논문은 여기 없습니다."""
    return {hit["arxiv_id"]: hit.get("ranked_by") or "unknown"
            for hit in response.get("related", [])}


def make_graph(subgraph, mechanism=None):
    """검색한 논문과 그 저자·인용 관계를 DOT 그래프로 반환합니다.

    `mechanism`이 있으면 **근거로 인용된 논문과 후보를 테두리로 가릅니다.** 후보를 지우지
    않습니다 - 공동인용으로 올라온 논문은 이 프로젝트가 재서 이긴 결과입니다(ADR-034).
    지워야 할 것은 논문이 아니라 "어떻게 들어왔는지 말하지 않는 것"이었습니다.
    """
    mechanism = mechanism or {}
    statements = set()
    with_authors = len(subgraph["papers"]) <= AUTHOR_LIMIT
    for paper in subgraph["papers"]:
        label = (f'{fill(paper["title"], width=34)}\n'
                 f'{paper["arxiv_id"]} ({paper["primary_category"] or "분류 없음"})')
        add_node(statements, paper["arxiv_id"], label,
                 style=MECHANISM_NODE_STYLE.get(mechanism.get(paper["arxiv_id"]), "solid"))
        if not with_authors:
            continue
        for name in paper["authors"]:
            # 점선은 인용이 아니라 저장된 저자 표기 연결입니다.
            add_node(statements, "author:" + name, fill(name, width=20), shape="ellipse")
            add_edge(statements, "author:" + name, paper["arxiv_id"], "AUTHORED", "dashed")
        hidden = paper["author_count"] - len(paper["authors"])
        if hidden > 0:
            more_id = f'more:{paper["arxiv_id"]}'
            add_node(statements, more_id, f"저자 {hidden}명 더", shape="plaintext")
            add_edge(statements, more_id, paper["arxiv_id"], "AUTHORED", "dotted")

    for link in subgraph["references"]:
        add_edge(statements, link["citing"], link["cited"], "REFERENCES")

    if not statements:
        return ""
    # 저자까지 가로로 늘어져 글자가 작아지지 않도록 위에서 아래로 배치합니다. 서로 이어지지
    # 않은 논문은 **3열 격자로 쌓습니다**(`packmode`). 안 그러면 한 줄에 전부 늘어서고, 작업대의
    # 가운데 칸에 맞춰 줄어들어 글자를 읽을 수 없었습니다(ADR-076).
    return "digraph {\nrankdir=TB;\npack=true;\npackmode=\"array_3\";\n" \
        "node [fontsize=10];\nedge [fontsize=9];\n" + \
        "\n".join(sorted(statements)) + "\n}"


def graph_legend(subgraph, max_nodes, mechanism=None):
    """`make_graph`가 실제로 그린 선과 테두리만 설명합니다.

    **두 탭이 같은 `make_graph`를 쓰는데 범례는 챗봇 쪽에만 있었습니다.** PageRank 탭은
    표시 수를 5로 내리면 저자 점선이 그려지는데 그 말이 없었습니다(`AUTHOR_LIMIT`이 6).

    그리고 챗봇 쪽은 논문이 6편을 넘으면 범례를 **갈아 끼우면서** 테두리 설명까지
    버렸습니다. 답변 그래프는 보통 6편을 넘으므로, 후보를 테두리로 가린다는 ADR-065의
    설명이 **거의 항상 사라지고 있었습니다.** 그래서 문장을 덮어쓰지 않고 그린 것마다
    한 조각씩 더합니다.
    """
    parts = ["실선: 논문 사이의 REFERENCES 인용."]
    if len(subgraph["papers"]) <= AUTHOR_LIMIT:
        parts.append("점선: 저장된 저자 표기 AUTHORED 연결.")
    else:
        parts.append(f"논문이 {AUTHOR_LIMIT}편을 넘어 글자가 작아지지 않도록 저자는 "
                     "생략했습니다.")
    if mechanism:
        # 후보가 섞여 있는데 테두리를 설명하지 않으면 "답변이 인용한 논문"으로 읽힙니다.
        parts.append("테두리가 실선인 논문은 답변이 근거로 인용한 것이고, 파선은 공동인용·"
                     "PageRank가 올린 후보, 점선은 임베딩이 채운 후보입니다 — 뒤의 둘은 "
                     "답변이 인용하지 않았습니다.")
    parts.append(f"논문은 최대 {max_nodes}개까지 그립니다.")
    return " ".join(parts)


def answer_text(response, body=None):
    """답변 문자열 뒤에 사용한 근거 ID를 표시합니다."""
    text = response["answer"] if body is None else body
    citations = ", ".join(response["evidence_ids"])
    return f"{text}\n\n근거 arXiv ID: {citations}" if citations else text


def show_answer(response, index):
    """영어 답변을 보여 주고, 버튼을 누르면 같은 답변의 한국어 번역으로 바꿉니다."""
    show_korean = st.session_state.korean.get(index, False)
    if show_korean:
        with st.spinner("답변을 한국어로 번역하고 있습니다."):
            try:
                body = translate(response["answer"])
            except Exception as exc:
                st.warning(model_outage.message(exc)
                           or f"번역 에러: {type(exc).__name__}. 영어 답변을 그대로 보여 줍니다.")
                body = response["answer"]
        st.markdown(answer_text(response, body))
    else:
        st.markdown(answer_text(response))

    label = "🇺🇸 영어 원문 보기" if show_korean else "🇰🇷 한국어로 번역"
    if st.button(label, key=f"translate:{index}"):
        st.session_state.korean[index] = not show_korean
        st.rerun()


def show_abstention(diagnostics):
    """보류를 고장이 아니라 판단으로 보여 줍니다.

    `ask()`는 그대로 예외를 올립니다. 보류를 답변으로 바꾸면 평가 하네스가 "답했다"로 세고
    게이트 기준선이 무효가 되므로, 바꾸는 것은 표시 계층뿐입니다(`abstain.py`).
    """
    told = abstain.explain(diagnostics)
    st.warning(f"**{told['headline']}**" + chr(10) + chr(10) + told["why"])
    st.markdown(f"**무엇이 걸렸나** — {told['what']}")
    st.markdown(f"**해 볼 것** — {told['advice']}")
    with st.expander("검사 기록"):
        st.markdown(
            f"- 걸린 단계: {told['stage'] or '기록되지 않음'}" + chr(10)
            + f"- 시도 횟수: {told['attempts']}" + chr(10)
            + f"- 요청 ID: `{told['request_id'] or '없음'}`"
        )
        if told["cyphers"]:
            # 모델이 실제로 무엇을 조회했는지 보여 줍니다. 멈춘 것이 "아무것도 안 한 것"이
            # 아님을 이것이 말해 줍니다.
            st.caption(f"모델이 실행한 조회 {len(told['cyphers'])}건")
            for cypher in told["cyphers"]:
                st.code(cypher, language="cypher")
        else:
            st.caption("실행된 그래프 조회가 없습니다.")


def generate_split(response):
    """`답변 생성`을 모델·도구·나머지로 가른 한 마디. 못 가르면 빈 문자열입니다.

    **`adds_up`이 false면 수를 적지 않습니다.** 잰 안쪽의 합이 노드 시간보다 크면 어딘가를
    두 번 센 것이고, 그 상태로 숫자를 보여 주면 틀린 분해를 측정값처럼 읽게 됩니다.
    """
    inside = response.get("generate_trace") or {}
    if not inside.get("adds_up", False):
        return ""
    if not inside.get("tool_calls") and not inside.get("model_turns"):
        return ""
    parts = [f'모델 {inside["model_seconds"]:.1f}초 ({inside["model_turns"]}턴)',
             f'도구 {inside["tools_seconds"]:.1f}초 ({inside["tool_calls"]}회)']
    if inside.get("unattributed_seconds", 0) >= 0.05:
        # 오류로 끝난 호출입니다. 쓴 시간을 버리면 나머지가 그만큼 부풀어 보입니다.
        parts.append(f'이름 못 붙임 {inside["unattributed_seconds"]:.1f}초')
    # 나머지는 **뺀 값**입니다. 잰 것과 같은 자리에 두면서 이름으로 구분합니다.
    parts.append(f'그 외 {max(inside["other_seconds"], 0.0):.1f}초')
    return " · ".join(parts)


def latency_line(response):
    """캡션 한 줄 — **합계와 실제로 시간을 쓴 노드**만. 0.00초 노드는 줄만 길게 합니다."""
    trace = response.get("graph_trace") or {}
    seconds = trace.get("seconds") or {}
    if not seconds:
        return ""
    spent = sorted(((value, node) for node, value in seconds.items() if value >= 0.05),
                   reverse=True)
    inside = generate_split(response)
    parts = [f'지연 합계 {trace["total_seconds"]:.1f}초']
    for value, node in spent[:2]:
        label = NODE_LABEL.get(node, node)
        note = f' ({inside or GENERATE_CONTAINS})' if node == "generate" else ""
        parts.append(f'{label} {value:.1f}초{note}')
    if (trace.get("attempts") or 1) > 1:
        # 재시도는 느린 답변의 흔한 이유입니다. 숨기면 합계만 보고 모델을 의심하게 됩니다.
        parts.append(f'시도 {trace["attempts"]}회')
    return " · ".join(parts)


def latency_rows(response):
    """노드별 표. **0.00초도 지우지 않습니다** - 공짜라는 것이 분해의 결론입니다."""
    trace = response.get("graph_trace") or {}
    seconds = trace.get("seconds") or {}
    visits = trace.get("visits") or {}
    inside = generate_split(response)
    rows = [{
        "노드": NODE_LABEL.get(node, node),
        "초": round(value, 2),
        "방문": visits.get(node, 1),
        "비고": (inside or GENERATE_CONTAINS) if node == "generate" else "",
    } for node, value in sorted(seconds.items(), key=lambda item: -item[1])]
    if rows:
        rows.append({"노드": "합계", "초": round(trace["total_seconds"], 2),
                     "방문": sum(visits.values()) or len(rows), "비고": ""})
    return rows


def tool_rows(response):
    """호출마다 몇 초였는지. **시간이 없는 호출은 빈칸으로 둡니다.**

    0을 적으면 재지 못한 것을 공짜라고 말하게 됩니다. 앞 시도의 호출은 마지막 응답에 남아
    있지 않으므로, 이 표의 합계는 `답변 생성`의 도구 몫보다 작을 수 있습니다.
    """
    calls = response.get("tool_calls") or []
    if not any("seconds" in call for call in calls):
        return []
    return [{
        "순서": number,
        "도구": TOOL_LABEL.get(call["name"], call["name"]),
        "초": round(call["seconds"], 2) if "seconds" in call else None,
        "결과": call.get("status", ""),
    } for number, call in enumerate(calls, 1)]


def route_marks(response):
    """캡션 한 줄을 **호출 기록과 `ranked_by`에서** 만듭니다.

    순수 함수로 뺀 이유는 이 줄이 두 가지를 거짓으로 적고 있었기 때문입니다 — 임베딩이
    관련 논문을 채운 경우에도 "벡터 의미 검색: 선택 안 됨"이라고 썼고, 공동인용이 주
    경로인 도구를 "개인화 PageRank: 선택됨"이라고만 불렀습니다. 테스트가 이 함수를
    직접 봅니다.
    """
    used = {call["name"] for call in response["tool_calls"]}
    found = found_by(response)
    # **벡터는 두 길로 들어옵니다.** `search_papers` 호출과, `rank_related_papers`가 빈자리를
    # 임베딩으로 채우는 경우입니다. 뒤쪽을 빼고 적어서 "선택 안 됨"이라고 쓰는 동안 임베딩이
    # 올린 논문이 그래프에 그려졌습니다.
    if "search_papers" in used and found.get("embedding"):
        vector = f'선택됨 + 관련 논문 보충 {found["embedding"]}편'
    elif "search_papers" in used:
        vector = "선택됨"
    elif found.get("embedding"):
        vector = f'관련 논문 보충에만 {found["embedding"]}편'
    else:
        vector = "선택 안 됨"
    # 이 도구를 "개인화 PageRank"로만 적으면 틀립니다 - 주 경로는 공동인용입니다(ADR-034).
    if found:
        related_mark = "관련 논문: " + " · ".join(
            f'{MECHANISM_LABEL.get(key, key)} {count}편'
            for key, count in sorted(found.items(), key=lambda item: -item[1]))
    elif "rank_related_papers" in used:
        related_mark = "관련 논문: 찾았지만 결과 없음"
    else:
        related_mark = "관련 논문: 선택 안 됨"
    return [f'한국어→영어 번역: {"선택됨" if "translate_to_english" in used else "선택 안 됨"}',
            f'벡터 의미 검색: {vector}',
            f'Text2Cypher: {"선택됨" if "search_graph" in used else "선택 안 됨"}',
            related_mark]


def route_line(route):
    """경로 판정과 **그 이유**를 한 줄로 적습니다. 모델을 부르기 전에 쓸 수 있습니다.

    `routing.classify`가 정하는 것은 **질문의 가족이 아닙니다.** `graph`냐 `any`냐와 그
    근거가 된 단서뿐입니다 - author·citing·coauthor 같은 가족 이름은 평가 데이터셋의
    `category`이고 질의 시점에는 없습니다(ADR-069에서 가족별 게이트를 기각한 이유가
    이것입니다). 그래서 "author 질문으로 봅니다"라고 쓰지 않고 경로와 단서만 적습니다.

    `route_marks`와 다릅니다. 저쪽은 **끝난 뒤** 어느 도구가 쓰였는지를 적고, 이쪽은
    **시작 전** 어디로 보낼지를 적습니다. 판정이 틀렸을 때 기다리지 않고 다시 물을 수
    있는 것이 이 줄의 목적입니다.
    """
    cues = list(route.get("cues") or ())
    vetoes = list(route.get("vetoes") or ())
    if route.get("name") == routing.GRAPH_ROUTE:
        head = "경로 판정: 저장된 관계만 묻는 질문으로 봅니다 — 그래프 경로"
    else:
        head = "경로 판정: 넓은 경로 — 의미 검색도 함께 씁니다"
    parts = [head]
    if cues:
        parts.append("단서: " + ", ".join(cues))
    # **단서가 있을 때만 적습니다.** 거부가 뒤집은 것이 있어야 "좁히지 않은 이유"입니다.
    # 단서가 없으면 거부가 없어도 넓은 경로이므로, 그때 거부를 적으면 **없던 인과를**
    # 만듭니다 - 배포 앱에서 "관련된 논문" 질문이 단서 0개에 거부 `similar` 하나로
    # 그렇게 나왔습니다(ADR-070).
    if vetoes and cues:
        parts.append("좁히지 않은 이유: " + ", ".join(vetoes))
    return " · ".join(parts)


def show_response(chatbot, response, index):
    """답변 하나를 세 칸 작업대로 그립니다 — 왼쪽 경로, 가운데 답변, 오른쪽 근거(ADR-076).

    전에는 한 칸에 답변·그래프·접힌 패널이 세로로 쌓여, 답을 읽은 뒤 근거를 보려면 스크롤이
    길었습니다. 근거(인용 ID·Cypher)를 오른쪽에 펼쳐 둡니다.

    **가운데를 먼저 그립니다.** 오른쪽이 가운데에서 읽은 부분 그래프를 씁니다. 칸은 자리가
    먼저 잡혀 있으므로 그리는 순서가 화면 순서를 바꾸지 않습니다.
    """
    route_col, answer_col, evidence_col = st.columns(WORKBENCH, gap="medium")
    with answer_col:
        subgraph = show_answer_column(chatbot, response, index)
    with route_col:
        show_route_column(chatbot, response)
    with evidence_col:
        show_evidence_column(response, subgraph)


def show_route_column(chatbot, response):
    """왼쪽 칸 — 어느 도구를 썼고 어디에 시간이 들었는지."""
    st.markdown("##### 이 답변의 경로")
    for mark in route_marks(response):
        st.caption(mark)
    if line := latency_line(response):
        st.caption(line)
    for item in response["translations"]:
        st.caption(f'검색어 번역: {item["source_text"]} → {item["english"]}')
    with st.expander("단계별 소요 시간"):
        # 이 답변을 낼 때 예열이 어느 상태였는지. **네 상태를 모두 적습니다** -
        # 위의 상태 줄은 끝나면 조용해야 하지만, 여기서 조용하면 "끝남"과
        # "시작 안 함"이 같아 보입니다. 실제로 한 번 그래서 갈라내지 못했습니다(ADR-067).
        st.caption(warmup.warmup_state_line(citation_warmup(chatbot).state()))
        if rows := latency_rows(response):
            st.write("노드별 소요 시간")
            st.dataframe(rows, hide_index=True)
            inside = response.get("generate_trace") or {}
            note = (f"기준선 90건의 p50은 {BASELINE_P50_SECONDS}초, 최대 "
                    f"{BASELINE_MAX_SECONDS}초입니다(11-46절). 0.00초인 노드는 실제로 "
                    "공짜입니다. ")
            if generate_split(response):
                # 안쪽을 쟀습니다. 그래도 **뺀 값과 잰 값**을 섞어 말하지 않습니다.
                note += (f"`답변 생성`의 모델·도구는 **잰 값**이고 `그 외`는 노드 시간에서 "
                         f"뺀 나머지입니다 — 스트리밍·부분 JSON 파싱·직렬화가 거기 "
                         f"있습니다(ADR-066). {MODEL_TURN_OPAQUE}.")
            elif inside and not inside.get("adds_up", True):
                # 두 번 센 상태입니다. 숫자를 보여 주는 것보다 틀렸다고 말하는 쪽이 낫습니다.
                note += ("**`답변 생성` 안쪽의 분해가 맞지 않습니다** — 잰 안쪽의 합이 노드 "
                         "시간보다 커서 어딘가를 두 번 셌습니다. 그래서 적지 않습니다.")
            else:
                note += ("**`답변 생성` 안쪽은 재지 않았습니다** — 번역·Cypher·벡터·모델 "
                         "턴의 몫을 가를 수 없습니다.")
            st.caption(note)
        if calls := tool_rows(response):
            st.write("도구 호출별 소요 시간")
            st.dataframe(calls, hide_index=True)
            st.caption(
                "이번 시도의 호출만 나옵니다. 재시도가 있었으면 앞 시도의 호출 시간은 위 "
                "`답변 생성`에만 들어 있습니다. 초가 빈칸이면 그 호출은 **재지 못한 것**이고 "
                "공짜라는 뜻이 아닙니다."
            )


def show_evidence_column(response, subgraph):
    """오른쪽 칸 — 답변이 무엇에 기대는지. **Cypher를 접지 않습니다**(ADR-076).

    이 프로젝트의 주장이 "Cypher 한 줄이 모델을 이겼다"인데, 그 Cypher가 접힌 패널 안쪽에
    있었습니다.
    """
    st.markdown("##### 근거")
    # 목록 위젯으로 적으면 25편이 25줄이 되어 칸을 다 차지합니다. 한 문단으로 적습니다.
    st.write(f'답변의 인용 arXiv ID {len(response["evidence_ids"])}편')
    st.caption(", ".join(response["evidence_ids"]) or "없음")
    if response["cypher"]:
        st.write("실행한 Cypher")
        st.code(response["cypher"], language="cypher", wrap_lines=True)
        st.dataframe(response["rows"], hide_index=True)
    for message in response.get("errors", []):
        # 실패한 Cypher는 에이전트가 고쳐서 다시 호출합니다. 시도 흔적을 남깁니다.
        st.write("Cypher 오류(에이전트가 다시 작성함):", message)
    # 선택 여부와 순서는 실제 호출 기록에서 읽습니다.
    with st.expander("도구 요청 순서"):
        for number, call in enumerate(response["tool_calls"], 1):
            spent = f' — {call["seconds"]:.2f}초' if "seconds" in call else ""
            st.write(f'{number}. {TOOL_LABEL.get(call["name"], call["name"])}{spent}')
            if call["name"] == "translate_to_english":
                st.write("번역할 문장:", call["args"].get("text", ""))
            elif call["name"] == "search_papers":
                st.write("검색어:", call["args"].get("query", ""))
            elif call["name"] == "rank_related_papers":
                st.write("시드 논문:", call["args"].get("arxiv_ids", []))
            if "output" in call:
                st.write("도구 응답:", call["status"])
                st.write(call["output"])

    if subgraph["papers"]:
        with st.expander("그래프에 그린 논문"):
            st.dataframe([{
                "arXiv ID": paper["arxiv_id"], "제목": paper["title"],
                "대표 분류": paper["primary_category"], "게재일": paper["published"][:10],
                "저자 수": paper["author_count"], "피인용": paper["cited_by"],
                "인용": paper["references_out"], "출처": paper["source"],
            } for paper in subgraph["papers"]], hide_index=True)


def show_answer_column(chatbot, response, index):
    """가운데 칸 — 답변, 그래프, 관련 논문과 비교. 그린 부분 그래프를 돌려줍니다."""
    show_answer(response, index)

    try:
        subgraph = subgraph_of(tuple(graph_ids(response)))
    except Exception as exc:
        subgraph = {"papers": [], "references": []}
        st.caption(f"그래프 조회 에러: {type(exc).__name__}. "
                   "답변과 근거는 그대로 확인할 수 있습니다.")
    mechanism = mechanism_of(response)
    graph = make_graph(subgraph, mechanism)
    if graph:
        st.write("검색된 그래프")
        st.graphviz_chart(graph, width="stretch")
        st.caption(graph_legend(subgraph, chatbot.MAX_GRAPH_NODES, mechanism))
    else:
        st.caption("이번 검색에서 시각화할 논문이 없습니다.")

    if response.get("related"):
        with st.expander("관련 논문 — 공동인용 · PageRank · 임베딩"):
            st.caption(
                "먼저 **같은 논문에 함께 인용된** 횟수로 뽑고(ADR-034에서 개인화 PageRank의 "
                "2.2배였습니다), 모자라면 개인화 PageRank, 그다음 임베딩으로 채웁니다. "
                "**점수의 단위가 그래서 행마다 다릅니다** — 아래 '찾은 방법'과 '점수 단위'를 "
                "같이 읽으세요. 답변이 근거로 인용한 논문과는 다릅니다."
            )
            st.dataframe([{
                "순위": hit["rank"],
                "찾은 방법": MECHANISM_LABEL.get(hit.get("ranked_by"), "분류 없음"),
                "점수": hit["score"],
                "점수 단위": MECHANISM_UNIT.get(hit.get("ranked_by"), ""),
                "공유 인용자": hit.get("shared_citers"),
                "arXiv ID": hit["arxiv_id"],
                "제목": hit["title"], "대표 분류": hit["primary_category"],
                "피인용": hit["cited_by"], "게재일": hit["published"][:10],
            } for hit in response["related"]], hide_index=True, column_config=RELATED_COLUMN)
            seed_ids = [call.get("output", {}).get("seed_ids", [])
                        for call in response["tool_calls"]
                        if call.get("name") == "rank_related_papers"]
            seeds = seed_ids[-1] if seed_ids else []
            paths = chatbot.explain_citation_paths(
                seeds, [hit["arxiv_id"] for hit in response["related"]]
            )
            st.markdown("##### 추천 경로")
            for hit in response["related"]:
                path = paths.get(hit["arxiv_id"])
                if path is None:
                    st.caption(
                        f'{hit["arxiv_id"]}: 시드에서 4단계 이내 인용 경로를 찾지 못했습니다.'
                    )
                else:
                    st.write(" → ".join(path["path"]))
                    st.caption("; ".join(
                        f'{edge["citing"]}가 {edge["cited"]}를 인용'
                        for edge in path["references"]
                    ))
                if st.button("논문 저장", key=f'save-related:{index}:{hit["arxiv_id"]}'):
                    paper = chatbot.fetch_paper_for_saving(hit["arxiv_id"])
                    if paper:
                        if _save_paper(paper):
                            st.toast("저장 목록에 추가했습니다.")
                            st.rerun()
                        else:
                            st.warning("저장 목록은 최대 20편입니다.")
                    else:
                        st.warning("논문 정보를 그래프에서 찾지 못했습니다.")

    # 관련 논문 패널과 **다른 질문**입니다 - 저쪽은 "지금 같이 읽히는 논문", 이쪽은
    # "앞으로 함께 인용될 논문"입니다. 섞으면 무엇을 본 것인지 말할 수 없어서 패널을
    # 따로 두고, 접은 채로 둡니다(ADR-038). 모델을 부르는 유일한 자리입니다.
    forecast_seeds = [
        call.get("output", {}).get("seed_ids", [])
        for call in response.get("tool_calls", [])
        if call.get("name") == "rank_related_papers"
    ]
    forecast_seed = (forecast_seeds[-1] or [None])[0] if forecast_seeds else None
    if forecast_seed:
        with st.expander("앞으로 함께 인용될 논문 (예측 모델)"):
            st.caption(
                "지금 같이 읽히는 논문이 아니라 **앞으로** 함께 인용될 논문입니다. "
                "학습 모델이 휴리스틱을 측정된 구간으로 이긴 유일한 과제여서 여기만 "
                "모델을 씁니다(ADR-031)."
            )
            if st.button("예측 보기", key=f"forecast:{index}:{forecast_seed}"):
                with st.spinner("후보를 모아 점수를 냅니다."):
                    result = chatbot.forecast_cocitation(forecast_seed)
                st.session_state[f"forecast-result:{index}"] = result
            result = st.session_state.get(f"forecast-result:{index}")
            if result and result.get("reason"):
                # 왜 비었는지 적습니다. 빈 패널만 보이면 고장과 구별되지 않습니다.
                st.info(result["reason"])
            elif result:
                st.dataframe([{
                    "arXiv ID": hit["arxiv_id"],
                    "점수": hit["score"],
                    "제목": hit.get("title", ""),
                    "지금 공동인용": hit["shared_cocitations"],
                    "임베딩 코사인": hit["embedding_cosine"],
                } for hit in result["papers"]], hide_index=True)
                model = result.get("model") or {}
                st.caption(
                    f'후보 {result.get("candidates", 0)}편에서 재정렬 · '
                    f'모델 지문 {model.get("digest")} · 스냅샷 '
                    f'{model.get("snapshot_version")} · 학습 과제 순위 MRR '
                    f'{model.get("ranking_mrr")}'
                )
                st.caption(
                    "**이 패널은 미래를 맞히려는 시도이고 정답은 아직 오지 않았습니다.** "
                    "모델이 통과한 기준은 과거 창에서 잰 것입니다."
                )

    if response["papers"]:
        with st.expander("벡터로 찾은 논문의 초록"):
            for hit in response["papers"]:
                st.write(f'{hit["arxiv_id"]} · {hit["title"]}')
                if st.button("논문 저장", key=f'save-search:{index}:{hit["arxiv_id"]}'):
                    # 벡터 검색 결과에는 저자와 게재일이 없습니다. 저장할 때 그래프에서
                    # 한 번 읽어 채워 둡니다 - 안 채우면 BibTeX의 `author`가 비고, 그
                    # 항목은 서지 도구에서 쓸 수 없습니다(ADR-042). 관련 논문 쪽은 이미
                    # 같은 질의를 씁니다.
                    # 이 경로는 전에 DB를 쓰지 않았으므로, 조회가 실패하면 **저장 자체가
                    # 실패하는 것이 아니라** 저자 칸만 비어야 합니다.
                    try:
                        details = chatbot.fetch_paper_for_saving(hit["arxiv_id"]) or {}
                    except Exception:  # noqa: BLE001
                        details = {}
                    if _save_paper({**details, **hit}):
                        st.toast("저장 목록에 추가했습니다.")
                        st.rerun()
                    else:
                        st.warning("저장 목록은 최대 20편입니다.")
                st.caption(f'Neo4j 벡터 유사도: {hit["score"]:.3f} (클수록 유사함) / '
                           f'분류: {", ".join(hit["categories"])}')
                st.write(hit["abstract"])
                st.link_button("arXiv PDF", hit["pdf_url"])

    show_paper_comparison(chatbot, response, index)
    return subgraph


def show_past_response(question, response, index):
    """지난 답변은 접어서 **답만** 보여 줍니다(ADR-076).

    화면은 다시 그릴 때마다 대화 기록을 전부 다시 그립니다. 지난 답변까지 세 칸으로 그리면
    답변마다 그래프 조회·인용 경로 조회가 반복되고, 스크롤도 답변 수만큼 길어집니다.
    Streamlit의 접힌 패널은 **안쪽도 실행합니다** - 그래서 접는 것만으로는 부족하고 안쪽을
    가볍게 둡니다. 근거가 필요하면 작업대로 다시 펼칩니다.
    """
    with st.expander(f"지난 답변 — {question[:80]}"):
        show_answer(response, index)
        if st.button("작업대에 펼치기", key=f"focus:{index}"):
            st.session_state.focus = index
            st.rerun()


def _save_paper(paper):
    """현재 브라우저 세션의 저장 논문 목록에 추가합니다."""
    saved = st.session_state.setdefault("saved_papers", [])
    updated = add_paper(saved, paper)
    if updated == saved and len(saved) >= 20 and not any(
        item.get("arxiv_id") == paper.get("arxiv_id") for item in saved
    ):
        return False
    st.session_state.saved_papers = updated
    return True


def _show_export_buttons(saved):
    """저장 목록을 BibTeX·CSV로 내려받습니다. 만드는 일은 `citations.py`가 합니다.

    **빈 칸을 세어 함께 적습니다.** 저장 경로에 따라 저자나 게재일이 없을 수 있고
    (`fetch_paper_for_saving` 조회가 실패한 경우), 그 사실이 보이지 않으면 쓰는 사람이
    불완전한 파일을 완전한 줄 알고 인용합니다(ADR-042).
    """
    stamp = datetime.now().strftime("%Y%m%d")
    bibtex_column, csv_column = st.columns(2)
    bibtex_column.download_button(
        "BibTeX로 내려받기",
        data=citations.to_bibtex(saved, note=f"내려받은 날짜 {stamp}").encode("utf-8"),
        file_name=f"arxiv_graph_rag_{stamp}.bib",
        mime="application/x-bibtex",
        key="saved-paper-export-bibtex",
        width="stretch",
    )
    csv_column.download_button(
        "CSV로 내려받기",
        # Excel이 BOM 없이는 한글 제목을 깨뜨립니다.
        data=citations.to_csv(saved).encode("utf-8-sig"),
        file_name=f"arxiv_graph_rag_{stamp}.csv",
        mime="text/csv",
        key="saved-paper-export-csv",
        width="stretch",
    )
    gaps = citations.missing(saved)
    if gaps:
        labels = {"author": "저자", "year": "게재 연도"}
        st.caption(" · ".join(
            f'{labels.get(name, name)}가 빈 항목 {len(ids)}건' for name, ids in gaps.items()
        ) + " — 그래프에서 읽지 못한 값은 추측하지 않고 비워 둡니다.")


def show_saved_papers(chatbot):
    """세션 저장 목록에서 논문을 관리하고 선택한 논문을 비교합니다."""
    saved = st.session_state.setdefault("saved_papers", [])
    with st.expander(f"저장한 논문 ({len(saved)}/20)", expanded=bool(saved)):
        st.caption("이 목록은 현재 브라우저 세션에만 보관됩니다.")
        if not saved:
            st.info("검색 결과나 추천 논문에서 ‘논문 저장’을 눌러 추가하세요.")
            return
        st.dataframe([{
            "arXiv ID": paper["arxiv_id"], "제목": paper.get("title", ""),
            "분류": ", ".join(paper.get("categories") or []),
            "초록": "있음" if paper.get("abstract") else "없음",
        } for paper in saved], hide_index=True, width="stretch")
        _show_export_buttons(saved)
        to_remove = st.multiselect(
            "목록에서 제거할 논문", options=[p["arxiv_id"] for p in saved],
            format_func=lambda paper_id: next(
                f'{paper_id} · {p.get("title", "")}' for p in saved
                if p["arxiv_id"] == paper_id
            ), key="saved-paper-remove-selection",
        )
        remove_col, clear_col = st.columns(2)
        if remove_col.button("선택 항목 제거", disabled=not to_remove,
                             key="saved-paper-remove"):
            st.session_state.saved_papers = [
                paper for paper in saved if paper["arxiv_id"] not in to_remove
            ]
            st.rerun()
        if clear_col.button("목록 전체 비우기", key="saved-paper-clear"):
            st.session_state.saved_papers = []
            st.rerun()

        options = [p["arxiv_id"] for p in saved
                   if isinstance(p.get("abstract"), str) and p["abstract"].strip()]
        selected = st.multiselect(
            "비교할 논문 (초록이 있는 논문 2~4편)", options=options,
            format_func=lambda paper_id: next(
                f'{paper_id} · {p.get("title", "")}' for p in saved
                if p["arxiv_id"] == paper_id
            ), max_selections=4, key="saved-paper-compare-selection",
        )
        selected_papers, selection_error = compare_selection(saved, selected)
        if st.button("저장한 논문 비교", key="saved-paper-compare",
                     disabled=bool(selection_error)):
            try:
                with st.spinner("초록을 바탕으로 저장한 논문을 비교하고 있습니다."):
                    result = chatbot.compare_papers(selected_papers)
                result_data = result.model_dump() if hasattr(result, "model_dump") else result
                st.session_state.saved_paper_comparison = result_data
                st.session_state.saved_paper_comparison_error = None
            except Exception as exc:
                st.session_state.saved_paper_comparison = None
                # 모델 한도·무응답이면 "선택을 확인하라"는 안내가 틀립니다. 이유를 그대로 둡니다.
                st.session_state.saved_paper_comparison_error = (
                    model_outage.message(exc) or type(exc).__name__)
        if selection_error and selected:
            st.caption(selection_error)
        failure = st.session_state.get("saved_paper_comparison_error")
        if failure:
            st.error(failure if failure in model_outage.MESSAGES.values()
                     else "논문 비교를 완료하지 못했습니다. 선택을 확인하고 다시 시도해 주세요.")
        result = st.session_state.get("saved_paper_comparison")
        if result and {p["arxiv_id"] for p in result.get("papers", [])} == set(selected):
            _show_comparison_result(result, saved)


def _show_comparison_result(result, papers):
    labels = {"research_question": "연구 질문", "method": "방법",
              "data_and_evaluation": "데이터·실험 설정", "key_findings": "주요 결과"}
    by_id = {paper["arxiv_id"]: paper for paper in papers}
    st.dataframe([{
        "arXiv ID": paper["arxiv_id"], "논문 제목": by_id[paper["arxiv_id"]].get("title", ""),
        **{labels[field]: paper[field]["text"] for field in labels},
    } for paper in result["papers"]], hide_index=True, width="stretch")


def show_paper_comparison(chatbot, response, index):
    """이 답변에서 벡터 검색된 논문을 선택해 초록 기반 비교를 보여 줍니다."""
    candidates = []
    seen_ids = set()
    for paper in response.get("papers", []):
        paper_id = paper.get("arxiv_id")
        title, abstract = paper.get("title"), paper.get("abstract")
        if (
            isinstance(paper_id, str)
            and paper_id.strip()
            and paper_id not in seen_ids
            and isinstance(title, str)
            and title.strip()
            and isinstance(abstract, str)
            and abstract.strip()
        ):
            candidates.append(paper)
            seen_ids.add(paper_id)
    if len(candidates) < 2:
        if response.get("papers"):
            st.info("논문 비교를 하려면 초록이 있는 검색 결과가 두 편 이상 필요합니다.")
        return

    st.markdown("#### 논문 비교")
    st.caption("검색 결과의 제목과 초록만 비교합니다. "
               "초록에서 확인할 수 없는 내용은 별도로 표시합니다.")
    papers_by_id = {paper["arxiv_id"]: paper for paper in candidates}
    selected_ids = st.multiselect(
        "비교할 논문 (2~4편)",
        options=list(papers_by_id),
        format_func=lambda paper_id: f"{paper_id} · {papers_by_id[paper_id]['title']}",
        max_selections=4,
        key=f"paper-comparison-selection:{index}",
    )
    selected_papers = [papers_by_id[paper_id] for paper_id in selected_ids]
    comparisons = st.session_state.setdefault("paper_comparisons", {})
    errors = st.session_state.setdefault("paper_comparison_errors", {})
    if st.button(
        "선택한 논문 비교",
        key=f"paper-comparison-run:{index}",
        disabled=not 2 <= len(selected_papers) <= 4,
    ):
        comparisons.pop(index, None)
        errors.pop(index, None)
        try:
            with st.spinner("초록을 바탕으로 논문을 비교하고 있습니다."):
                result = chatbot.compare_papers(selected_papers)
            result_data = result.model_dump() if hasattr(result, "model_dump") else result
            if not isinstance(result_data, dict) or not isinstance(result_data.get("papers"), list):
                raise ValueError("비교 결과 형식이 올바르지 않습니다.")
            comparisons[index] = {
                "selected_ids": list(selected_ids),
                "result": result_data,
            }
        except Exception as exc:
            errors[index] = {
                "selected_ids": list(selected_ids),
                "message": model_outage.message(exc) or (
                    f"논문 비교를 완료하지 못했습니다 ({type(exc).__name__}). "
                    "선택을 확인하고 다시 시도해 주세요."),
            }

    error = errors.get(index)
    if error and error["selected_ids"] == selected_ids:
        st.error(error["message"])
    stored = comparisons.get(index)
    if not stored or stored["selected_ids"] != selected_ids:
        return

    labels = {
        "research_question": "연구 질문",
        "method": "방법",
        "data_and_evaluation": "데이터·실험 설정",
        "key_findings": "주요 결과",
    }
    rows = []
    for paper in stored["result"]["papers"]:
        source = papers_by_id[paper["arxiv_id"]]
        rows.append({
            "arXiv ID": paper["arxiv_id"],
            "논문 제목": source["title"],
            "arXiv 페이지": f"https://arxiv.org/abs/{quote(paper['arxiv_id'], safe='./')}",
            **{labels[field]: paper[field]["text"] for field in labels},
        })
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={"arXiv 페이지": st.column_config.LinkColumn("arXiv 페이지")},
    )


def show_family_board(baseline):
    """분야별 표. 수 옆에 `n`을 둡니다 - 분야마다 6문항, 3회 실행입니다(`family_board`)."""
    stable = baseline.get("ragas_stable_families") or []
    rows = family_board.load_rows(stable)
    if not rows:
        return
    st.markdown("##### 분야별 — 분야마다 6문항 × 3회")
    if family_board.at_ceiling(rows):
        st.caption(
            "**검색·근거 지표는 다섯 분야 모두 1.0이고 실패는 0건입니다.** 이 30문항으로는 "
            "그 지표에서 분야를 가르지 못합니다. 분야끼리 다른 것은 RAGAS뿐이고, 그중 "
            "`context_precision`은 '게이트' 열이 '예'인 분야만 봅니다 - 기준선의 "
            "`ragas_stable_families`가 정하고, 나머지 분야는 품질과 무관하게 움직였습니다(ADR-017)."
        )

    def judged(row, name):
        low, high = row[f"{name}_range"] or (None, None)
        if row[name] is None:
            return "—"
        return f"{row[name]:.3f}" if low == high else f"{row[name]:.3f} ({low:.2f}~{high:.2f})"

    st.dataframe([{
        "분야": row["family"],
        "n": f'{row["cases"]}×{row["runs"]}',
        "hit@5": row["hit_at_k"],
        "근거 재현율": row["answer_evidence_recall"],
        "실패율": row["failure_rate"],
        "faithfulness": judged(row, "faithfulness"),
        "answer_relevancy": judged(row, "answer_relevancy"),
        "context_precision": judged(row, "context_precision"),
        "게이트": "예" if row["gated_context_precision"] else "아니오",
    } for row in rows], hide_index=True)


def show_quality_board():
    """게이트·하한·기각 기록을 한 화면에. 여기서 숫자를 만들지 않습니다.

    이 저장소의 측정이 보고서 90여 개와 결정 기록 51건에 흩어져 있어서, 처음 보는 사람이
    확인할 방법이 없었습니다. 읽기는 `quality_board.py`가 하고 여기서는 그리기만 합니다 -
    나눠 두면 접속 없이 테스트할 수 있습니다(ADR-040).
    """
    baseline = quality_board.load(quality_board.BASELINE)
    if baseline is None:
        st.info("기준선 파일이 없습니다: baselines/eval_baseline.json")
        return

    head = quality_board.summary(baseline)
    st.caption(
        f'기준선 {head["generated_at"][:19]} · {head["dataset"]} {head["cases"]}건을 '
        f'{head["runs"]}회 실행 · 분할 `{head["metrics_split"]}` · '
        f'RAGAS 판정 `{head["judge_model"]}`'
    )
    columns = st.columns(4)
    columns[0].metric("게이트가 보는 지표", head["gated_metrics"])
    columns[1].metric("사례", head["cases"])
    columns[2].metric("안정 사례", head["stable_cases"])
    columns[3].metric("변동폭보다 좁은 하한", len(head["tight_floors"]))

    if head["prompts_drifted"]:
        st.warning(
            "지금 코드의 프롬프트가 기준선을 만든 실행과 다릅니다: "
            f'`{"`, `".join(head["prompts_drifted"])}` — 아래 숫자는 **이 코드가 낸 점수가 '
            "아닙니다.** 평가를 다시 돌리고 기준선을 다시 써야 합니다(ADR-041)."
        )

    st.markdown("##### 규칙 — 하나라도 걸리면 머지되지 않습니다")
    st.dataframe(
        [{"규칙": name, "무엇을 막는가": what} for name, what in quality_board.RULES],
        hide_index=True,
    )

    st.markdown("##### 지표 · 하한 · 여유")
    st.caption(
        "**여유가 변동폭보다 좁으면 그 하한은 품질이 아니라 운을 잽니다**(ADR-004). "
        "지금은 그런 지표가 없습니다." if not head["tight_floors"] else
        f'**여유가 변동폭보다 좁은 지표가 있습니다: {", ".join(head["tight_floors"])}**'
    )
    st.dataframe([{
        "지표": row["metric"],
        "측정": row["measured"],
        "하한": row["floor"],
        "여유": row["headroom"],
        "허용 하락폭": row["allowed_drop"],
        "3회 변동폭": row["observed_spread"],
    } for row in quality_board.gate_rows(baseline)], hide_index=True)

    with st.expander("하한을 그 값으로 정한 이유"):
        for name, why in (baseline.get("floor_rationale") or {}).items():
            st.markdown(f"**`{name}`** — {why}")

    basis = quality_board.basis_rows(quality_board.load(quality_board.FLOOR_BASIS))
    if basis:
        with st.expander("RAGAS 하한이 무언가를 막는지 — 결함을 심어 확인"):
            st.caption(
                "하한에 필요한 질문은 '평균이 하한보다 큰가'가 아닙니다(ADR-004·ADR-043). "
                "**정상이던 실행 중 최악이 하한을 넘는가**, 그리고 **결함을 심으면 걸리는가** "
                "입니다. 둘째가 아니면 그 하한은 상수에 걸려 있습니다."
            )
            st.dataframe([{
                "지표": row["metric"],
                "하한": row["floor"],
                "본 실행": row["runs_seen"],
                "최악 정상 실행": row["worst_normal_run"],
                "하한까지 여유": row["margin"],
                "최악도 넘는가": "예" if row["survives"] else "아니오",
                "결함을 심으면": ", ".join(f"{value:.4f}" for value in row["with_defect"]),
                "그때 걸리는가": "예" if row["defect_trips_it"] else "아니오",
            } for row in basis], hide_index=True)

    with st.expander("이 점수를 낸 프롬프트"):
        st.caption(
            "`sha256` 앞 12자입니다. **지문이 다르면 다른 프롬프트**이고, 게이트의 "
            "`prompt_version` 규칙이 그것을 막습니다 — `git_sha`로는 대신할 수 없습니다"
            "(ADR-018·ADR-041)."
        )
        st.dataframe([{
            "프롬프트": row["prompt"],
            "기준선": row["baseline"],
            "지금 코드": row["code"],
            "같은가": {True: "예", False: "아니오"}.get(row["same"], "—"),
        } for row in quality_board.prompt_rows(baseline)], hide_index=True)

    show_family_board(baseline)

    st.markdown("##### 평가 하네스 밖에서 지키는 선")
    for row in quality_board.slot_rows(baseline):
        label = f'{row["label"]} · `{row["metric"]}`'
        if row["variant"]:
            label += f' (변형 `{row["variant"]}`)'
        st.markdown(f'{label} — 측정 **{row["measured"]}**, 하한 **{row["floor"]}**')
        st.caption(f'검사: `{row["checked_by"]}`')

    future = quality_board.load(quality_board.FUTURE_STUDY)
    if future:
        st.markdown("##### 관련 논문 변형 비교 (미래 라벨 과제)")
        st.caption(
            f'시드 {future.get("cases")}건 · 피처 창 {future.get("feature_cut")} 이전 · '
            "현직은 배포된 경로입니다."
        )
        st.dataframe([{
            "변형": ("**" + row["variant"] + "** (배포)") if row["deployed"] else row["variant"],
            "recall@5": row["recall_at_k"],
            "hit@5": row["hit_at_k"],
            "MRR": row["reciprocal_rank"],
            "현직 대비 recall@5": row["vs_incumbent"],
        } for row in quality_board.variant_rows(future)], hide_index=True)

        degrees = quality_board.degree_rows(future)
        if degrees:
            st.markdown("###### 연결 정도로 나누면")
            st.caption("**집계만 보면 두 체제가 섞입니다**(ADR-037). recall@5입니다.")
            st.dataframe(degrees, hide_index=True)

    st.markdown("##### 재서 기각한 모델")
    st.caption("무엇을 쓰는지만큼 무엇을 왜 안 쓰는지가 이 저장소의 내용입니다.")
    st.dataframe(
        [{"모델": name, "자리": slot, "결과": result, "기록": record}
         for name, slot, result, record in quality_board.REJECTED],
        hide_index=True,
    )
    st.caption(
        "전체 기록: [docs/MODELS.md](https://github.com/rndigkwk/arxiv_graph_RAG/blob/main/docs/MODELS.md) · "  # noqa: E501
        "[결정 기록](https://github.com/rndigkwk/arxiv_graph_RAG/tree/main/docs/adr)"
    )


def show_home(chatbot):
    """스키마와 메타그래프로 이 그래프가 무엇인지 먼저 보여 줍니다."""
    from src import metagraph

    counts = graph_counts()
    st.subheader("이 앱이 질문하는 지식 그래프")
    st.write("arXiv 컴퓨터과학 논문과 그 논문들이 인용한 논문을 Neo4j Aura에 적재한 그래프입니다. "
             "챗봇 탭에서 이 그래프에 질문하고, PageRank 순위 탭에서 인용망의 중심을 봅니다.")

    left, middle, right, far = st.columns(4)
    left.metric("논문", f'{counts["papers"]:,}')
    middle.metric("저자 이름", f'{counts["authors"]:,}')
    right.metric("AUTHORED", f'{counts["authored"]:,}')
    far.metric("REFERENCES", f'{counts["references"]:,}')

    st.markdown("#### 스키마 메타그래프")
    st.caption("라벨 하나가 점 하나입니다. 점 크기와 선 굵기는 지금 Aura에서 센 실제 개수이고, "
               "점·선에 마우스를 올리면 속성과 의미가 나옵니다.")
    st.plotly_chart(metagraph.schema_figure(counts), width="stretch")
    st.code("\n".join(["(:ArxivAuthorName {name})-[:AUTHORED]->(:ArxivPaper)",
                       "(:ArxivPaper)-[:REFERENCES]->(:ArxivPaper)"]), language="text")

    with st.expander("ArxivPaper의 속성"):
        st.dataframe([
            {"속성": "arxiv_id", "타입": "STRING", "설명": "버전을 뗀 고유 ID (2303.11366)"},
            {"속성": "title", "타입": "STRING", "설명": "논문 제목 (영어)"},
            {"속성": "abstract", "타입": "STRING", "설명": "초록 (영어). 임베딩 입력의 일부"},
            {"속성": "published", "타입": "DATETIME", "설명": "게재일. 기간 필터에 씁니다"},
            {"속성": "pdf_url", "타입": "STRING", "설명": "arXiv PDF 링크"},
            {"속성": "primary_category", "타입": "STRING",
             "설명": "대표 분류 하나 (cs.AI). 인덱스 있음"},
            {"속성": "categories", "타입": "LIST<STRING>", "설명": "대표 분류를 포함한 모든 분류"},
            {"속성": "source", "타입": "STRING",
             "설명": "ai=수집한 논문, reference=인용으로 딸려온 논문"},
            {"속성": "embedding", "타입": "LIST<FLOAT>", "설명": "제목+초록 768차원 벡터"},
        ], hide_index=True, width="stretch")
        st.caption("분류는 노드가 아니라 ArxivPaper의 속성입니다. "
                   "그래서 :ArxivCategory 노드는 없습니다. "
                   "ArxivAuthorName은 이름 표기 노드라 동명이인을 구분하지 않습니다.")

    report = quality_report()
    if report is None:
        st.info("커뮤니티 메타그래프는 `notebooks/aura_graph_rag_quality_report.ipynb`를 실행해 "
                "`reports/aura_graph_rag_quality_report.json`을 만들면 여기에 나타납니다.")
        return

    st.markdown("#### 커뮤니티 메타그래프")
    st.caption("인용망을 Leiden으로 나눈 커뮤니티 중 상위 5개입니다. 점 하나가 커뮤니티 하나이고, "
               "점 크기는 논문 수, 선 굵기는 두 커뮤니티 사이의 상호 인용 수입니다.")
    st.plotly_chart(metagraph.community_figure(report), width="stretch")
    st.dataframe([{
        "커뮤니티": row["label"], "논문": row["papers"], "주분류": row["primary_category"],
        "보조분류": row["secondary_category"] or "-",
    } for row in report["communities_top5"]], hide_index=True, width="stretch")

    st.caption('출처: reports/aura_graph_rag_quality_report.json · '
               f'생성 {report["generated_at"]} · '
               f'Leiden {report["community_mode"]}')


# 처음 보여 줄 분류입니다. 수집 창이 `cs.AI` 기준이라 가장 많지만 **전부는 아닙니다** -
# 대표 분류로 세면 cs.AI가 30.2%이고, cs.CL·cs.LG·cs.CV가 그 뒤로 48%를 더 가집니다
# (131종, 2026-10-26). 이 값을 고정으로 두는 동안 이 탭은 저장된 논문의 30%만 보여
# 주고 있었습니다.
DEFAULT_CATEGORY = "cs.AI"
# 고를 수 있게 둘 분류 수입니다. 131개 전부를 세로로 늘어놓으면 고르기가 더 어렵습니다.
CATEGORY_CHOICES = 10


def scopes(code):
    """계산 범위 라디오의 문구입니다. 고른 분류가 문장에 들어갑니다."""
    return {f"전체 인용망에서 계산한 뒤 {code}만 남기기": "global",
            f"{code} 논문끼리의 인용만으로 계산하기": "subgraph"}


@st.cache_data(show_spinner=False)
def category_options():
    """논문 수 상위 분류 목록입니다. 데이터가 늘어도 손으로 고치지 않습니다."""
    return load_chatbot().top_categories(CATEGORY_CHOICES)


def gds_module():
    """GDS 엔진을 고를 때만 무거운 의존성을 불러옵니다."""
    from src import gds_pagerank

    return gds_pagerank


@st.cache_data(show_spinner=False)
def gds_ranking(code, limit, include_secondary, scope, session_name):
    """같은 조건은 세션에 다시 묻지 않습니다."""
    return gds_module().category_pagerank(code, limit, include_secondary, scope, session_name)


def pick_session():
    """붙을 GDS 세션을 정합니다. 새 세션은 명시적으로 눌러야 만듭니다(과금 시작)."""
    gds_pagerank = gds_module()
    try:
        live = [row for row in gds_pagerank.list_sessions() if row["status"] == "Ready"]
    except Exception as exc:
        st.error(f"Aura API 조회 에러: {type(exc).__name__}. .env의 CLIENT_ID·CLIENT_SECRET과 "
                 "네트워크를 확인하세요.")
        return None

    try:
        tier = gds_pagerank.instance_tier()
    except Exception:
        tier = {"type": None, "free": False, "name": None}
    free = gds_pagerank.FREE_TIER
    if tier["free"]:
        st.success(f'이 인스턴스는 AuraDB Free(`{tier["type"]}`)라 Graph Analytics **Free 등급**'
                   f'이고 세션 요금이 청구되지 않습니다. 한도는 메모리 {free["memory"]}, '
                   f'동시 {free["sessions"]}개, 세션 최대 {free["max_hours"]}시간입니다.')
    else:
        st.warning(f'유료 등급(`{tier["type"] or "확인 불가"}`)입니다. '
                   '세션은 **GB-분**으로 청구되고 '
                   "최소 10분이 붙습니다. 예: 2GB 세션을 25분 쓰면 50 GB-분.")

    if live:
        names = [row["name"] for row in live]
        chosen = st.selectbox("붙을 세션", names)
        info = next(row for row in live if row["name"] == chosen)
        st.caption(f'메모리 {info["memory"]} · 생성 {info["created"]:%Y-%m-%d %H:%M} UTC · '
                   f'만료 {info["expiry"]:%Y-%m-%d %H:%M} UTC')
        st.caption("이미 떠 있는 세션에 붙습니다. 새로 만들지 않습니다.")
        return chosen

    note = ("Free 등급이라 요금은 청구되지 않습니다."
            if tier["free"] else "만드는 시점부터 GB-분으로 청구됩니다.")
    st.info(f'떠 있는 세션이 없습니다. {note} '
            f'{gds_pagerank.SESSION_TTL.seconds // 60}분 동안 쓰지 않으면 스스로 내려갑니다.'
            + (f' Free 등급은 동시에 {free["sessions"]}개까지만 띄울 수 있습니다.'
               if tier["free"] else ""))
    if st.button("새 GDS 세션 만들기"):
        with st.spinner("세션을 만들고 있습니다. 몇 분 걸릴 수 있습니다."):
            try:
                gds_pagerank.connect(gds_pagerank.DEFAULT_SESSION_NAME, create=True)
            except Exception as exc:
                st.error(f"세션 생성 에러: {type(exc).__name__}. {exc}")
                return None
        st.rerun()
    return None


def show_pagerank(chatbot):
    """고른 분류의 PageRank 순위를 표와 그래프로 보여 줍니다."""
    st.subheader("인용망 PageRank 순위")
    st.caption("저장된 REFERENCES 인용망에서 고른 분류 논문의 PageRank를 계산합니다. "
               "인용을 많이 받을수록, 그리고 중요한 논문에게 인용받을수록 점수가 올라갑니다.")

    engine = st.radio(
        "계산 엔진",
        ["직접 계산 (scipy)", "Aura GDS 세션 (Graph Analytics)"],
        horizontal=True,
        help="두 엔진의 순위는 같습니다. 점수 눈금만 다릅니다.",
    )
    use_gds = engine.startswith("Aura")

    session_name = None
    if use_gds:
        session_name = pick_session()
        if session_name is None:
            return
    elif not st.session_state.get("pagerank_ready"):
        st.write(f"논문 {chatbot.paper_count:,}편과 인용 관계를 한 번 읽어 옵니다. "
                 "처음 한 번만 20초쯤 걸리고, 그 뒤로는 바로 나옵니다.")
        if st.button("인용망 불러오고 PageRank 계산"):
            with st.spinner("인용망을 읽고 PageRank를 계산하고 있습니다."):
                chatbot.global_pagerank()
            st.session_state.pagerank_ready = True
            st.rerun()
        return

    try:
        options = category_options()
    except Exception as exc:
        st.caption(f"분류 목록 조회 에러: {type(exc).__name__}. {DEFAULT_CATEGORY}로 봅니다.")
        options = [{"code": DEFAULT_CATEGORY, "papers": None}]
    labels = {f'{row["code"]}' + (f' ({row["papers"]:,}편)' if row["papers"] else ""):
              row["code"] for row in options}
    default = next((label for label, value in labels.items()
                    if value == DEFAULT_CATEGORY), next(iter(labels)))
    code = labels[st.selectbox("분류", list(labels),
                               index=list(labels).index(default),
                               help="논문 수 상위 "
                                    f"{CATEGORY_CHOICES}개 분류입니다. 대표 분류 기준입니다.")]

    choices = scopes(code)
    left, right = st.columns([3, 2])
    with left:
        scope = choices[st.radio("계산 범위", list(choices), horizontal=False)]
    with right:
        limit = st.slider("표시할 논문 수", 5, 50, 20, step=5)
        include_secondary = st.checkbox("부가 분류까지 포함", value=True)

    try:
        with st.spinner("순위를 계산하고 있습니다."):
            if use_gds:
                result = gds_ranking(code, limit, include_secondary, scope, session_name)
            else:
                result = ranking(code, limit, include_secondary, scope)
    except Exception as exc:
        st.error(f"순위 계산 에러: {type(exc).__name__}. {exc}")
        if use_gds:
            st.caption("세션이 만료됐을 수 있습니다. "
                       "엔진을 직접 계산으로 바꾸면 바로 볼 수 있습니다.")
        return

    field = "categories" if include_secondary else "primary_category"
    st.caption(f'{code} 논문 {result["paper_count"]:,}편 ({field} 기준) 중 상위 '
               f'{len(result["papers"])}편입니다.')
    if scope == "global":
        st.caption("전체 인용망에서 계산했으므로 다른 분류 논문에게 받은 인용도 점수에 들어갑니다.")
    else:
        st.caption(f"{code} 논문끼리의 인용만 남긴 하위 그래프에서 계산했습니다. "
                   "이 분야 안에서의 중요도입니다.")
    if use_gds:
        st.caption(f'GDS 세션 `{result["session_name"]}`에서 계산했습니다. '
                   f'투영한 노드 {result["projected_nodes"]:,}개 / '
                   f'관계 {result["projected_relationships"]:,}개.')

    if not result["papers"]:
        st.info("이 조건에서는 인용으로 이어진 논문이 없어 순위를 만들 수 없습니다.")
        return

    st.dataframe([{
        "순위": paper["rank"], "PageRank": paper["score"], "arXiv ID": paper["arxiv_id"],
        "제목": paper["title"], "대표 분류": paper["primary_category"],
        "피인용": paper["cited_by"], "게재일": paper["published"][:10],
    } for paper in result["papers"]], hide_index=True, width="stretch",
        column_config=SCORE_COLUMN)
    st.caption("PageRank 순위와 피인용 순위는 다릅니다. 인용해 준 논문이 중요할수록 "
               "피인용 수가 적어도 위로 올라갑니다.")

    top_ids = [paper["arxiv_id"] for paper in result["papers"]]
    try:
        subgraph = subgraph_of(tuple(top_ids))
    except Exception as exc:
        st.caption(f"그래프 조회 에러: {type(exc).__name__}. 표는 그대로 확인할 수 있습니다.")
        return
    graph = make_graph(subgraph)
    if graph:
        st.write(f"상위 {len(subgraph['papers'])}편 사이의 인용 관계")
        st.graphviz_chart(graph, width="stretch")
        st.caption(graph_legend(subgraph, chatbot.MAX_GRAPH_NODES))

    with st.expander("PageRank를 어떻게 계산했나요"):
        st.markdown(f"""
공통 설정: 감쇠 계수 `{chatbot.DAMPING}`, 최대 `{chatbot.PAGERANK_ITERATIONS}`회 반복,
대상은 `(:ArxivPaper)-[:REFERENCES]->(:ArxivPaper)` 인용망입니다.
인용 방향 그대로(인용하는 쪽 → 인용된 쪽) 계산하므로 점수는 인용받는 쪽으로 흐릅니다.

| | 직접 계산 | Aura GDS 세션 |
|---|---|---|
| 계산 위치 | 이 앱의 `scipy` 희소 행렬 | Aura Graph Analytics 세션 |
| 비용 | 없음 | **Free 등급은 청구 없음**, 유료 등급은 GB-분(최소 10분) |
| 처음 한 번 | 인용망 읽기 약 20초 | 세션 접속 약 8초 + 원격 투영 |
| 이후 한 번 | 0.1초 미만 | 3~5초 |
| 점수 눈금 | 합이 1이 되게 정규화 | GDS 기본값(정규화하지 않음) |
| 고립 논문 | 인용이 없는 논문도 포함 | 인용에 참여한 논문만 투영됨 |

**두 엔진의 순위는 같습니다.** cs.AI 상위 5편을 두 범위 모두에서 대조해 확인했습니다.
점수 숫자만 눈금이 달라 직접 비교하면 안 됩니다.

GDS 세션이 돌려주는 `nodeId`는 AuraDB의 내부 노드 id와 같아서, 순위에 오른 id만 조회 전용
Cypher로 되읽어 제목·분류를 붙입니다. **DB에 `pagerank` 속성을 쓰지 않습니다.**

챗봇 탭의 **개인화 PageRank**는 항상 직접 계산을 씁니다. 질문마다 도는 기능이라 세션 왕복
3~5초가 대화 응답에 그대로 붙기 때문입니다. 그때는 텔레포트를 질문한 논문에만 몰아주고
인용 방향을 무시한 무방향 인용망을 써서, 전체에서 유명한 논문이 아니라 **그 논문 주변에서**
중요한 논문을 찾습니다.
""")


def sidebar():
    """예시 질문만 둡니다. 스키마 설명은 홈 탭으로 옮겼습니다."""
    with st.sidebar:
        st.subheader("질문 예시")
        st.markdown("""
- 지식 그래프 추론에 그래프 신경망을 쓰는 논문을 찾아 줘
- cs.AI 논문 중에서 피인용이 가장 많은 논문 5편은?
- Yoshua Bengio가 쓴 논문이 있어?
- 대규모 언어 모델 에이전트를 다루는 cs.CL 논문을 알려 줘
- Attention Is All You Need를 인용한 논문을 알려 줘
- 2026년에 나온 cs.AI 논문 중 피인용이 많은 3편은?
- 2303.11366 논문과 관련된 논문을 알려 줘 (개인화 PageRank)
""")
        st.caption("답변은 영어로 나오고, 답변 아래의 번역 버튼으로 한국어를 볼 수 있습니다. "
                   "그래프 구조와 속성은 `홈` 탭에서 봅니다.")


def main():
    """준비를 마친 뒤 챗봇 탭과 PageRank 순위 탭을 그립니다."""
    # **임포트가 아니라 여기서 로그를 설정합니다**(ADR-053). 모듈 최상단에 두면 `app`을
    # 임포트하는 것만으로 전역 로깅이 바뀌고, 실제로 테스트 두 개가 그 부작용을 받았습니다.
    # Streamlit은 이 스크립트를 `__main__`으로 돌리므로 여기도 진입점입니다.
    #
    # `load_chatbot()`보다 앞섭니다 - `chatbot`은 임포트 시점에 Aura에 붙으므로 설정이
    # 늦으면 앱이 뜰 때의 줄이 형식 없이 나갑니다.
    #
    # 두 번 불러도 우리 핸들러가 늘지 않습니다. Streamlit은 상호작용마다 이 스크립트를
    # 다시 돌리므로 그 보장이 없으면 재실행 수만큼 줄이 복제됩니다.
    logs.configure()
    st.set_page_config(page_title="arXiv GraphRAG 챗봇", page_icon="📄", layout="wide")
    st.title("arXiv GraphRAG 챗봇")
    st.caption("질문에 따라 한국어→영어 번역, 인용·저자 관계 조회, 초록 의미 검색을 선택합니다.")
    try:
        with st.spinner("그래프와 검색 저장소를 준비하고 있습니다."):
            chatbot = load_chatbot()
    except Exception as exc:
        st.error(f"실시간 그래프 연결을 준비하지 못했습니다 ({type(exc).__name__}).")
        st.info("Aura 인스턴스가 일시 중지됐거나 접속 정보·벡터 인덱스가 "
                "준비되지 않았을 수 있습니다. "
                "APP_README.md의 연결 점검 절차를 확인한 뒤 다시 시도하세요.")
        st.markdown("#### 데이터베이스 없이 보는 데모 화면")
        st.caption("아래는 저장소에 포함된 화면 캡처입니다. 실시간 검색 결과가 아닙니다.")
        st.image("data/home.jpg", caption="홈 화면: 지식 그래프 구조와 적재 현황")
        st.link_button("앱 설정 및 오류 대응", "https://github.com/rndigkwk/arxiv_graph_RAG/blob/main/APP_README.md")
        st.stop()

    # 홈 화면을 읽는 동안 인용망을 올립니다. 이 줄이 없으면 첫 질문이 그 값을 혼자 냅니다
    # (배포 실측 46.9초 대 22.7초, ADR-067). 배경이므로 아래 렌더링을 막지 않습니다.
    citation_warmup(chatbot)

    sidebar()
    # 배포 표시는 배포 점검이 읽습니다(scripts/visit_deployed_app.py). 커밋을 두 번 읽어
    # 화면과 검색 모듈이 같은 코드인지 봅니다 - 다르면 재부팅이 필요하다는 뜻입니다.
    loaded = getattr(chatbot, "LOADED_COMMIT", release.OLDER)
    deployed, stale = release.stamp(release.commit(), loaded)
    # 커밋이 달라도 `src/`의 내용이 같으면 옛 코드가 아닙니다. Streamlit Cloud는 `src/`를
    # 건드리지 않은 머지에서도 모듈을 다시 임포트하지 않습니다(ADR-072 고침, 이슈 #56).
    if stale and source_check.same_code(getattr(chatbot, "LOADED_SOURCE", None)):
        stale = None
    st.caption(f"적재된 논문 {chatbot.paper_count:,}편 / 모델 {chatbot.llm_model} / {deployed}")
    if stale:
        st.warning(stale)

    home_tab, chat_tab, rank_tab, gate_tab = st.tabs(
        ["홈", "챗봇", "PageRank 순위", "품질 게이트"]
    )
    with home_tab:
        show_home(chatbot)
    with rank_tab:
        show_pagerank(chatbot)
    with chat_tab:
        chat(chatbot)
    with gate_tab:
        show_quality_board()


def answer_question(chatbot, question):
    """대기 중인 질문 하나를 처리하고, 성공하면 대화 기록에 넣습니다."""
    # 재실행되어도 같은 질문을 두 번 묻지 않도록 먼저 비웁니다.
    st.session_state.pending = None
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        # 모델을 부르기 전에 어디로 보낼지 적습니다. 판정은 모델 없이 끝나므로(0원) 기다리는
        # 동안 보이고, 틀렸으면 기다리지 않고 다시 물을 수 있습니다.
        st.caption(route_line(routing.classify(question).as_dict()))
        answer_area = st.empty()
        try:
            with st.spinner("근거를 검색하고 답변을 생성하고 있습니다."):
                response, history = chatbot.ask(
                    question, st.session_state.history, on_answer=answer_area.markdown,
                )
            # 스트리밍 영역을 검증한 최종 답변·그래프로 교체해 본문이 중복되지 않게 합니다.
            with answer_area.container():
                # 이 답변이 기록에서 갖게 될 위치를 번역 버튼 키로 씁니다.
                show_response(chatbot, response, len(st.session_state.messages) + 1)
        except ValueError as exc:
            answer_area.empty()
            # 근거 검증 실패를 빈 검색 결과나 연결 에러로 안내하지 않습니다.
            if hasattr(exc, "diagnostics"):
                show_abstention(exc.diagnostics())
            else:
                st.error(str(exc))
            return
        except Exception as exc:
            answer_area.empty()
            # 모델이 실패한 것을 "검색 에러 - 연결을 확인하라"로 적으면 할 일이 틀립니다(ADR-073).
            st.error(model_outage.message(exc)
                     or f"검색 에러: {type(exc).__name__}. 연결 상태를 확인하고 다시 질문하세요.")
            return
    # 성공적으로 처리한 질문만 저장하므로 중단된 질문은 다음 대화에 섞이지 않습니다.
    st.session_state.history = history
    st.session_state.focus = None
    st.session_state.messages.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "response": response},
    ])
    # 다시 그려야 앞 답변이 접힙니다. 안 그러면 이 실행에서는 앞 답변과 새 답변이 둘 다 세 칸으로
    # 남습니다 - 배포 전 로컬 화면에서 그렇게 보였습니다(ADR-076). 새 답변은 기록에서 다시
    # 그려지고, 그래프 조회는 캐시가 받습니다.
    st.rerun()


def clear_chat():
    """화면 기록과 모델 기록, 번역 상태, 대기 중인 질문을 함께 비웁니다."""
    st.session_state.messages = []
    st.session_state.history = []
    st.session_state.korean = {}
    st.session_state.pending = None
    st.session_state.focus = None
    st.session_state.paper_comparisons = {}
    st.session_state.paper_comparison_errors = {}
    for key in list(st.session_state.keys()):
        if isinstance(key, str) and key.startswith("paper-comparison-selection:"):
            del st.session_state[key]


def workbench_index(messages, focus):
    """세 칸으로 펼칠 답변의 위치. 고른 것이 없거나 없어진 위치면 **가장 최근 답변**입니다."""
    answers = [index for index, message in enumerate(messages)
               if message["role"] == "assistant"]
    if focus in answers:
        return focus
    return answers[-1] if answers else None


def question_before(messages, index):
    """답변 바로 앞의 질문입니다. 접은 답변의 제목으로 씁니다."""
    for message in reversed(messages[:index]):
        if message["role"] == "user":
            return message["content"]
    return ""


def chat(chatbot):
    """대화 기록을 유지하며 질문을 처리합니다.

    st.chat_input은 탭 안에서는 화면 하단에 고정되지 않고 호출한 자리에 그려집니다.
    그래서 두 컨테이너로 자리를 먼저 잡습니다. 입력창을 그린 뒤에 답변을 만들어도
    답변은 위 컨테이너에 들어가므로, 입력창은 생성 중에도 계속 맨 아래에 남습니다.
    """
    # 화면용 기록과 모델용 메시지를 분리하되 둘 다 브라우저 세션에 저장합니다.
    if "messages" not in st.session_state:
        clear_chat()
    if st.button("대화 지우기"):
        clear_chat()
    # 예열이 **끝났으면 아무 말도 하지 않습니다.** 기다리는 중이거나 실패했을 때만 적습니다.
    if line := warmup.warmup_line(citation_warmup(chatbot).state()):
        st.caption(line)
    show_saved_papers(chatbot)

    messages_area = st.container()
    input_area = st.container()

    messages = st.session_state.messages
    focus = workbench_index(messages, st.session_state.get("focus"))
    with messages_area:
        for index, message in enumerate(messages):
            with st.chat_message(message["role"]):
                if message["role"] == "user":
                    st.write(message["content"])
                elif index == focus:
                    show_response(chatbot, message["response"], index)
                else:
                    show_past_response(question_before(messages, index),
                                       message["response"], index)

    # 답변을 만드는 동안에도 입력창이 보이도록 먼저 그립니다. 자리는 위에서 이미 잡혔습니다.
    with input_area:
        question = st.chat_input("예: 지식 그래프 추론에 그래프 신경망을 쓰는 논문을 찾아 줘")

    if st.session_state.get("pending"):
        with messages_area:
            answer_question(chatbot, st.session_state.pending)

    if question and question.strip():
        # 이번 실행에서는 그리지 않고, 다시 실행해 기록 아래·입력창 위에 이어 그립니다.
        st.session_state.pending = question.strip()
        st.rerun()


if __name__ == "__main__":
    main()
