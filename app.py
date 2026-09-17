"""실행: uv run streamlit run app.py --server.fileWatcherType none"""
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


@st.cache_data(show_spinner=False)
def graph_counts():
    """홈 화면 메타그래프에 쓸 노드·관계 수입니다."""
    return load_chatbot().graph_counts()


@st.cache_data(show_spinner=False)
def quality_report():
    """품질 리포트 노트북이 저장한 JSON입니다. 없으면 None."""
    import metagraph

    return metagraph.load_report()


@st.cache_data(show_spinner=False)
def ranking(code, limit, include_secondary, scope):
    """같은 조건의 PageRank 순위는 한 번만 계산합니다."""
    return load_chatbot().category_pagerank(code, limit, include_secondary, scope)


# 점수가 1e-4 수준이라 기본 표시로는 전부 0.0000이 됩니다. 지수 표기로 보여 줍니다.
SCORE_COLUMN = {"PageRank": st.column_config.NumberColumn("PageRank", format="%.3e")}


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
             f'Text2Cypher: {"선택됨" if "search_graph" in used else "선택 안 됨"}',
             f'개인화 PageRank: {"선택됨" if "rank_related_papers" in used else "선택 안 됨"}']
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
                      "search_papers": "벡터 의미 검색",
                      "rank_related_papers": "개인화 PageRank"}
        # 선택 여부와 순서는 실제 호출 기록에서 읽습니다.
        st.write("도구 요청 순서")
        for number, call in enumerate(response["tool_calls"], 1):
            st.write(f'{number}. {tool_names.get(call["name"], call["name"])}')
            if call["name"] == "translate_to_english":
                st.write("번역할 문장:", call["args"].get("text", ""))
            elif call["name"] == "search_papers":
                st.write("검색어:", call["args"].get("query", ""))
            elif call["name"] == "rank_related_papers":
                st.write("시드 논문:", call["args"].get("arxiv_ids", []))
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

    if response.get("related"):
        with st.expander("개인화 PageRank로 찾은 관련 논문"):
            st.caption("질문한 논문에서 출발해 인용 관계를 따라 퍼뜨린 점수입니다. "
                       "전체에서 유명한 논문이 아니라 그 논문 주변에서 중요한 논문입니다.")
            st.dataframe([{
                "순위": hit["rank"], "PageRank": hit["score"], "arXiv ID": hit["arxiv_id"],
                "제목": hit["title"], "대표 분류": hit["primary_category"],
                "피인용": hit["cited_by"], "게재일": hit["published"][:10],
            } for hit in response["related"]], hide_index=True, column_config=SCORE_COLUMN)

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


def show_home(chatbot):
    """스키마와 메타그래프로 이 그래프가 무엇인지 먼저 보여 줍니다."""
    import metagraph

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
            {"속성": "primary_category", "타입": "STRING", "설명": "대표 분류 하나 (cs.AI). 인덱스 있음"},
            {"속성": "categories", "타입": "LIST<STRING>", "설명": "대표 분류를 포함한 모든 분류"},
            {"속성": "source", "타입": "STRING", "설명": "ai=수집한 논문, reference=인용으로 딸려온 논문"},
            {"속성": "embedding", "타입": "LIST<FLOAT>", "설명": "제목+초록 768차원 벡터"},
        ], hide_index=True, width="stretch")
        st.caption("분류는 노드가 아니라 ArxivPaper의 속성입니다. 그래서 :ArxivCategory 노드는 없습니다. "
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

    st.caption(f'출처: reports/aura_graph_rag_quality_report.json · 생성 {report["generated_at"]} · '
               f'Leiden {report["community_mode"]}')


# 이 페이지는 cs.AI 한 분류만 다룹니다.
CATEGORY = "cs.AI"

SCOPES = {f"전체 인용망에서 계산한 뒤 {CATEGORY}만 남기기": "global",
          f"{CATEGORY} 논문끼리의 인용만으로 계산하기": "subgraph"}


def gds_module():
    """GDS 엔진을 고를 때만 무거운 의존성을 불러옵니다."""
    import gds_pagerank

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
        st.warning(f'유료 등급(`{tier["type"] or "확인 불가"}`)입니다. 세션은 **GB-분**으로 청구되고 '
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
    """cs.AI 논문의 PageRank 순위를 표와 그래프로 보여 줍니다."""
    st.subheader(f"{CATEGORY} 인용망 PageRank 순위")
    st.caption(f"저장된 REFERENCES 인용망에서 {CATEGORY} 논문의 PageRank를 계산합니다. "
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

    code = CATEGORY
    left, right = st.columns([3, 2])
    with left:
        scope = SCOPES[st.radio("계산 범위", list(SCOPES), horizontal=False)]
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
            st.caption("세션이 만료됐을 수 있습니다. 엔진을 직접 계산으로 바꾸면 바로 볼 수 있습니다.")
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
        subgraph = chatbot.fetch_subgraph(top_ids)
    except Exception as exc:
        st.caption(f"그래프 조회 에러: {type(exc).__name__}. 표는 그대로 확인할 수 있습니다.")
        return
    graph = make_graph(subgraph)
    if graph:
        st.write(f"상위 {len(subgraph['papers'])}편 사이의 인용 관계")
        st.graphviz_chart(graph, width="stretch")
        st.caption(f"상위 {chatbot.MAX_GRAPH_NODES}편까지만 그리고, 그 논문들 사이의 인용만 "
                   "화살표로 표시합니다.")

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

    home_tab, chat_tab, rank_tab = st.tabs(["홈", "챗봇", "PageRank 순위"])
    with home_tab:
        show_home(chatbot)
    with rank_tab:
        show_pagerank(chatbot)
    with chat_tab:
        chat(chatbot)


def answer_question(chatbot, question):
    """대기 중인 질문 하나를 처리하고, 성공하면 대화 기록에 넣습니다."""
    # 재실행되어도 같은 질문을 두 번 묻지 않도록 먼저 비웁니다.
    st.session_state.pending = None
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


def clear_chat():
    """화면 기록과 모델 기록, 번역 상태, 대기 중인 질문을 함께 비웁니다."""
    st.session_state.messages = []
    st.session_state.history = []
    st.session_state.korean = {}
    st.session_state.pending = None


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

    messages_area = st.container()
    input_area = st.container()

    with messages_area:
        for index, message in enumerate(st.session_state.messages):
            with st.chat_message(message["role"]):
                if message["role"] == "user":
                    st.write(message["content"])
                else:
                    show_response(chatbot, message["response"], index)

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
