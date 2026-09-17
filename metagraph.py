"""홈 탭에 그릴 Plotly 메타그래프입니다.

두 가지를 그립니다.

1. **스키마 메타그래프** — 라벨 하나를 점 하나로 줄인 그림입니다. 점 크기와 선 굵기는
   Aura에서 방금 센 실제 노드·관계 수입니다.
2. **커뮤니티 메타그래프** — `notebooks/aura_graph_rag_quality_report.ipynb`의
   `plot_community_metagraph`(matplotlib)를 Plotly로 옮긴 것입니다. Leiden 커뮤니티 하나가
   점 하나이고, 선 굵기는 두 커뮤니티 사이의 상호 REFERENCES 인용 수입니다.
   커뮤니티 계산에는 GDS가 필요하므로, 앱에서 다시 돌리지 않고 그 노트북이 저장한
   `reports/aura_graph_rag_quality_report.json`을 읽어 그립니다.
"""
import json
import math
from pathlib import Path

import networkx as nx
import plotly.graph_objects as go

import chatbot

REPORT_PATH = chatbot.root_dir / "reports" / "aura_graph_rag_quality_report.json"

# 품질 리포트 노트북과 같은 색을 씁니다.
PAPER_COLOR = "#2a78d6"
AUTHOR_COLOR = "#eb6834"
EDGE_COLOR = "#9aa0a6"
LAYOUT_SEED = 42        # 노트북과 같은 시드라 배치가 실행마다 같습니다.


def blank_layout(figure, height, x_range=None):
    """축·격자를 지우고 Streamlit 테마 위에 투명하게 얹습니다.

    x_range를 주면 그 범위로 고정합니다. 자동 범위는 점의 좌표만 보고 정해서
    바깥에 놓은 글자가 잘리기 때문입니다.
    """
    figure.update_layout(
        height=height, showlegend=False, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, range=x_range),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
        hoverlabel=dict(font_size=12),
    )
    return figure


def marker_size(value, largest, smallest=34, biggest=96):
    """개수 차이가 커서 넓이가 아니라 제곱근에 비례하게 키웁니다."""
    if not largest:
        return smallest
    return smallest + (biggest - smallest) * math.sqrt(value / largest)


def edge_width(value, largest, thinnest=1.5, thickest=9.0):
    """선 굵기도 같은 이유로 제곱근 비례입니다."""
    if not largest:
        return thinnest
    return thinnest + (thickest - thinnest) * math.sqrt(value / largest)


def arc_points(center, radius, start, end, steps=60):
    """자기참조 고리를 그릴 원호 좌표입니다."""
    xs, ys = [], []
    for step in range(steps + 1):
        angle = start + (end - start) * step / steps
        xs.append(center[0] + radius * math.cos(angle))
        ys.append(center[1] + radius * math.sin(angle))
    return xs, ys


def schema_figure(counts):
    """라벨·관계를 점과 선으로 줄인 스키마 메타그래프입니다. 수치는 지금 DB의 실제 값입니다."""
    author = (-1.15, 0.0)
    paper = (0.75, 0.0)
    biggest_node = max(counts["papers"], counts["authors"])
    biggest_edge = max(counts["authored"], counts["references"])

    figure = go.Figure()
    # AUTHORED: 저자 이름 → 논문
    figure.add_trace(go.Scatter(
        x=[author[0], paper[0]], y=[author[1], paper[1]], mode="lines",
        line=dict(color=EDGE_COLOR, width=edge_width(counts["authored"], biggest_edge)),
        hoverinfo="skip"))
    figure.add_trace(go.Scatter(
        x=[(author[0] + paper[0]) / 2], y=[0.0], mode="text",
        text=[f'AUTHORED<br>{counts["authored"]:,}'], textposition="top center",
        textfont=dict(size=11, color=EDGE_COLOR),
        hovertemplate=f'AUTHORED<br>저자 이름 → 논문<br>{counts["authored"]:,}개<extra></extra>'))

    # REFERENCES: 논문 → 논문 자기참조 고리
    loop_x, loop_y = arc_points((paper[0] + 0.5, 0.0), 0.5, -2.5, 2.5)
    figure.add_trace(go.Scatter(
        x=loop_x, y=loop_y, mode="lines",
        line=dict(color=EDGE_COLOR, width=edge_width(counts["references"], biggest_edge)),
        hoverinfo="skip"))
    figure.add_trace(go.Scatter(
        x=[paper[0] + 1.32], y=[0.0], mode="text",
        text=[f'REFERENCES<br>{counts["references"]:,}'],
        textfont=dict(size=11, color=EDGE_COLOR),
        hovertemplate=f'REFERENCES<br>인용하는 논문 → 인용된 논문<br>'
                      f'{counts["references"]:,}개<extra></extra>'))

    figure.add_trace(go.Scatter(
        x=[author[0], paper[0]], y=[author[1], paper[1]], mode="markers+text",
        marker=dict(
            size=[marker_size(counts["authors"], biggest_node),
                  marker_size(counts["papers"], biggest_node)],
            color=[AUTHOR_COLOR, PAPER_COLOR], line=dict(color="white", width=2)),
        text=[f'ArxivAuthorName<br>{counts["authors"]:,}',
              f'ArxivPaper<br>{counts["papers"]:,}'],
        textposition="bottom center", textfont=dict(size=12),
        customdata=[
            "이름 표기 하나가 노드 하나입니다. 동명이인은 구분하지 않습니다.<br>속성: name",
            "논문 하나가 노드 하나입니다.<br>속성: arxiv_id, title, abstract, published,<br>"
            "pdf_url, primary_category, categories, source, embedding",
        ],
        hovertemplate="<b>%{text}</b><br>%{customdata}<extra></extra>"))

    # 화살표는 관계 방향을 보여 줍니다. 분류는 노드가 아니라 논문의 속성입니다.
    figure.add_annotation(x=paper[0] - 0.12, y=0.0, ax=author[0] + 0.3, ay=0.0,
                          xref="x", yref="y", axref="x", ayref="y",
                          showarrow=True, arrowhead=3, arrowsize=1.3, arrowwidth=1.6,
                          arrowcolor=EDGE_COLOR)
    # 자기참조 고리 오른쪽의 REFERENCES 글자까지 들어가도록 범위를 직접 잡습니다.
    return blank_layout(figure, 320, x_range=[-1.75, 2.85])


def load_report(path=REPORT_PATH):
    """품질 리포트 노트북이 저장한 JSON을 읽습니다. 없으면 None."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not report.get("communities_top5") or not report.get("community_citation_flow"):
        return None
    return report


def community_figure(report):
    """커뮤니티 하나를 점 하나로 줄인 메타그래프입니다. 노트북 그림과 같은 배치·의미입니다."""
    rows = report["communities_top5"]
    flow = report["community_citation_flow"]

    meta = nx.Graph()
    for row in rows:
        meta.add_node(row["label"], **row)
    labels = [row["label"] for row in rows]
    for index, source in enumerate(labels):
        for target in labels[index + 1:]:
            # flow는 {인용한 쪽: {인용된 쪽: 건수}} 구조입니다. 양방향을 합쳐 상호 인용으로 봅니다.
            citations = flow.get(source, {}).get(target, 0) + flow.get(target, {}).get(source, 0)
            if citations:
                meta.add_edge(source, target, citations=citations)

    position = nx.spring_layout(meta, seed=LAYOUT_SEED, k=1.2)
    figure = go.Figure()

    citations = [meta.edges[edge]["citations"] for edge in meta.edges]
    biggest_flow = max(citations, default=1)
    for source, target in meta.edges:
        value = meta.edges[(source, target)]["citations"]
        start, end = position[source], position[target]
        figure.add_trace(go.Scatter(
            x=[start[0], end[0]], y=[start[1], end[1]], mode="lines",
            line=dict(color=EDGE_COLOR, width=edge_width(value, biggest_flow)),
            hoverinfo="skip"))
        # 선 자체에는 hover가 잘 잡히지 않아 중점에 투명한 점을 놓습니다.
        figure.add_trace(go.Scatter(
            x=[(start[0] + end[0]) / 2], y=[(start[1] + end[1]) / 2], mode="markers",
            marker=dict(size=16, color="rgba(0,0,0,0)"),
            hovertemplate=f"{source} ↔ {target}<br>상호 인용 {value:,}건<extra></extra>"))

    papers = [meta.nodes[label]["papers"] for label in meta.nodes]
    biggest = max(papers, default=1)
    figure.add_trace(go.Scatter(
        x=[position[label][0] for label in meta.nodes],
        y=[position[label][1] for label in meta.nodes],
        mode="markers+text",
        marker=dict(size=[marker_size(meta.nodes[label]["papers"], biggest, 38, 104)
                          for label in meta.nodes],
                    color=PAPER_COLOR, line=dict(color="white", width=2)),
        text=[f'{meta.nodes[label]["primary_category"]}<br>{meta.nodes[label]["papers"]:,}편'
              for label in meta.nodes],
        textposition="middle center", textfont=dict(size=10, color="white"),
        customdata=[[label, meta.nodes[label]["primary_category"],
                     meta.nodes[label]["secondary_category"] or "-",
                     meta.nodes[label]["papers"]] for label in meta.nodes],
        hovertemplate="<b>%{customdata[0]}</b><br>주분류 %{customdata[1]}"
                      "<br>보조분류 %{customdata[2]}<br>논문 %{customdata[3]:,}편<extra></extra>"))
    return blank_layout(figure, 460)
