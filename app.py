"""실행: uv run streamlit run app/app.py --server.fileWatcherType none"""
import json
import re
from textwrap import fill

import streamlit as st


# 연결·검색기는 공유하고, 각 사용자의 대화는 아래 session_state에 따로 저장합니다.
@st.cache_resource
def load_chatbot():
    """화면을 다시 그려도 연결과 검색 저장소를 재사용합니다."""
    # 준비 에러도 main에서 안내할 수 있게 여기서 검색 모듈을 불러옵니다.
    import chatbot

    return chatbot


@st.cache_data(show_spinner=False)
def translate(text):
    """같은 답변을 여러 번 눌러도 한 번만 번역합니다."""
    return load_chatbot().translate_to_korean(text)


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
    if not response["evidence_ids"]:
        ids += [pid for row in response["rows"] for pid in row.get("evidence_ids", [])]
        ids += [hit["arxiv_id"] for hit in response["papers"]]
    return list(dict.fromkeys(ids))


# 논문이 많으면 저자까지 그릴 때 가로로 늘어져 글자를 읽을 수 없습니다.
AUTHOR_LIMIT = 6


def make_graph(subgraph):
    """검색한 논문과 그 저자·인용 관계를 DOT 그래프로 반환합니다."""
    statements = set()
    with_authors = len(subgraph["papers"]) <= AUTHOR_LIMIT
    for paper in subgraph["papers"]:
        label = (f'{fill(paper["title"], width=34)}\n'
                 f'{paper["arxiv_id"]} ({paper["primary_category"] or "분류 없음"})')
        add_node(statements, paper["arxiv_id"], label)
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
    # 저자까지 가로로 늘어져 글자가 작아지지 않도록 위에서 아래로 배치합니다.
    return "digraph {\nrankdir=TB;\nnode [fontsize=10];\nedge [fontsize=9];\n" + \
        "\n".join(sorted(statements)) + "\n}"


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
                st.warning(f"번역 에러: {type(exc).__name__}. 영어 답변을 그대로 보여 줍니다.")
                body = response["answer"]
        st.markdown(answer_text(response, body))
    else:
        st.markdown(answer_text(response))

    label = "🇺🇸 영어 원문 보기" if show_korean else "🇰🇷 한국어로 번역"
    if st.button(label, key=f"translate:{index}"):
        st.session_state.korean[index] = not show_korean
        st.rerun()


def show_response(chatbot, response, index):
    """실제 검색 방식, 답변, 검색 그래프, 상세 근거를 함께 보여줍니다."""
    used = {call["name"] for call in response["tool_calls"]}
    marks = [f'한국어→영어 번역: {"선택됨" if "translate_to_english" in used else "선택 안 됨"}',
             f'벡터 의미 검색: {"선택됨" if "search_papers" in used else "선택 안 됨"}',
             f'Text2Cypher: {"선택됨" if "search_graph" in used else "선택 안 됨"}']
    st.caption(" | ".join(marks))
    for item in response["translations"]:
        st.caption(f'검색어 번역: {item["source_text"]} → {item["english"]}')
    show_answer(response, index)

    try:
        subgraph = chatbot.fetch_subgraph(graph_ids(response))
    except Exception as exc:
        subgraph = {"papers": [], "references": []}
        st.caption(f"그래프 조회 에러: {type(exc).__name__}. 답변과 근거는 그대로 확인할 수 있습니다.")
    graph = make_graph(subgraph)
    if graph:
        st.write("검색된 그래프")
        st.graphviz_chart(graph, width="stretch")
        note = ("실선: 논문 사이의 REFERENCES 인용. 점선: 저장된 저자 표기 AUTHORED 연결. "
                f"논문은 최대 {chatbot.MAX_GRAPH_NODES}개까지 그립니다.")
        if len(subgraph["papers"]) > AUTHOR_LIMIT:
            note = (f"실선: 논문 사이의 REFERENCES 인용. 논문이 {AUTHOR_LIMIT}편을 넘어 글자가 작아지지 "
                    "않도록 저자는 생략했습니다. 저자 수는 아래 표에서 확인하세요.")
        st.caption(note)
    else:
        st.caption("이번 검색에서 시각화할 논문이 없습니다.")

    with st.expander("검색 과정과 근거 확인"):
        st.write("답변의 인용 arXiv ID:", response["evidence_ids"])
        tool_names = {"translate_to_english": "한국어→영어 번역",
                      "search_graph": "Text2Cypher 관계 조회",
                      "search_papers": "벡터 의미 검색"}
        # 선택 여부와 순서는 실제 호출 기록에서 읽습니다.
        st.write("도구 요청 순서")
        for number, call in enumerate(response["tool_calls"], 1):
            st.write(f'{number}. {tool_names.get(call["name"], call["name"])}')
            if call["name"] == "translate_to_english":
                st.write("번역할 문장:", call["args"].get("text", ""))
            elif call["name"] == "search_papers":
                st.write("검색어:", call["args"].get("query", ""))
            if "output" in call:
                st.write("도구 응답:", call["status"])
                st.write(call["output"])
        if response["cypher"]:
            st.write("실행한 Cypher")
            st.code(response["cypher"], language="cypher")
            st.dataframe(response["rows"], hide_index=True)
        for message in response.get("errors", []):
            # 실패한 Cypher는 에이전트가 고쳐서 다시 호출합니다. 시도 흔적을 남깁니다.
            st.write("Cypher 오류(에이전트가 다시 작성함):", message)

    if response["papers"]:
        with st.expander("벡터로 찾은 논문의 초록"):
            for hit in response["papers"]:
                st.write(f'{hit["arxiv_id"]} · {hit["title"]}')
                st.caption(f'Neo4j 벡터 유사도: {hit["score"]:.3f} (클수록 유사함) / '
                           f'분류: {", ".join(hit["categories"])}')
                st.write(hit["abstract"])
                st.link_button("arXiv PDF", hit["pdf_url"])

    if subgraph["papers"]:
        with st.expander("그래프에 그린 논문"):
            st.dataframe([{
                "arXiv ID": paper["arxiv_id"], "제목": paper["title"],
                "대표 분류": paper["primary_category"], "게재일": paper["published"][:10],
                "저자 수": paper["author_count"], "피인용": paper["cited_by"],
                "인용": paper["references_out"], "출처": paper["source"],
            } for paper in subgraph["papers"]], hide_index=True)


def sidebar():
    """스키마 요약과 예시 질문을 접어 둡니다."""
    with st.sidebar:
        st.subheader("이 그래프의 스키마")
        st.code("(:ArxivAuthorName {name})-[:AUTHORED]->(:ArxivPaper)\n"
                "(:ArxivPaper)-[:REFERENCES]->(:ArxivPaper)\n\n"
                "ArxivPaper: arxiv_id, title, abstract, published,\n"
                "            pdf_url, primary_category, categories, source",
                language="text")
        st.caption("분류는 노드가 아니라 ArxivPaper의 속성입니다.")
        st.subheader("질문 예시")
        st.markdown("""
- 지식 그래프 추론에 그래프 신경망을 쓰는 논문을 찾아 줘
- cs.AI 논문 중에서 피인용이 가장 많은 논문 5편은?
- Yoshua Bengio가 쓴 논문이 있어?
- 대규모 언어 모델 에이전트를 다루는 cs.CL 논문을 알려 줘
- 저장된 ZZZ없는주제 논문을 알려 줘 (근거 부족 안내 확인)
""")
        st.caption("답변은 영어로 나오고, 답변 아래의 번역 버튼으로 한국어를 볼 수 있습니다.")


def main():
    """한 페이지에서 대화 기록을 유지하며 질문을 처리합니다."""
    st.set_page_config(page_title="arXiv GraphRAG 챗봇", page_icon="📄", layout="wide")
    st.title("arXiv GraphRAG 챗봇")
    st.caption("질문에 따라 한국어→영어 번역, 인용·저자 관계 조회, 초록 의미 검색을 선택합니다.")
    try:
        with st.spinner("그래프와 검색 저장소를 준비하고 있습니다."):
            chatbot = load_chatbot()
    except Exception as exc:
        st.error(f"준비 에러: {type(exc).__name__}. 저장소 루트의 .env 연결 정보와 "
                 f"Aura 인스턴스 상태, 적재 노트북 실행 여부를 확인하세요.")
        st.stop()

    sidebar()
    st.caption(f"적재된 논문 {chatbot.paper_count:,}편 / 모델 {chatbot.llm_model}")

    # 화면용 기록과 모델용 메시지를 분리하되 둘 다 브라우저 세션에 저장합니다.
    if "messages" not in st.session_state:
        st.session_state.messages = []
        st.session_state.history = []
        st.session_state.korean = {}
    if st.button("대화 지우기"):
        st.session_state.messages = []
        st.session_state.history = []
        st.session_state.korean = {}

    for index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.write(message["content"])
            else:
                show_response(chatbot, message["response"], index)

    question = st.chat_input("예: 지식 그래프 추론에 그래프 신경망을 쓰는 논문을 찾아 줘")
    if not question or not question.strip():
        return
    question = question.strip()
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
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
            st.error(str(exc))
            return
        except Exception as exc:
            answer_area.empty()
            st.error(f"검색 에러: {type(exc).__name__}. 연결 상태를 확인하고 다시 질문하세요.")
            return
    # 성공적으로 처리한 질문만 저장하므로 중단된 질문은 다음 대화에 섞이지 않습니다.
    st.session_state.history = history
    st.session_state.messages.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "response": response},
    ])


if __name__ == "__main__":
    main()
