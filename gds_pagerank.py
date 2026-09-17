"""Aura Graph Analytics 세션으로 PageRank를 계산하는 선택 엔진입니다.

기본 엔진은 `chatbot.py`의 직접 계산이고, 이 모듈은 화면에서 GDS 엔진을 고를 때만 불러옵니다.
세션에는 수명 제한이 있어(Free 등급 최대 4시간, TTL 30분) 언젠가 반드시 끊기므로,
항상 동작하는 직접 계산을 기본값으로 둡니다.

과금: AuraDB **Free 등급에서는 세션이 청구되지 않습니다**(2GB·동시 1개·최대 4시간 한도).
유료 등급에서는 GB-분으로 청구되고 최소 10분이 붙습니다.
그래서 인스턴스 등급을 조회해 화면에 알리고, 새 세션 생성은 명시적으로 누를 때만 합니다.
https://neo4j.com/docs/aura/billing/billing-dimensions/#_aura_graph_analytics

`.env`(또는 같은 값이 든 `.streamlit/secrets.toml`)의 `CLIENT_ID`·`CLIENT_SECRET`으로
Aura API에 인증하고, 세션은 `NEO4J_URI`의 인스턴스에 붙습니다.

결과 매핑: 세션이 돌려주는 `nodeId`는 AuraDB의 내부 노드 id와 같습니다. 그래서 순위에 오른
id만 조회 전용 Cypher로 되읽어 `arxiv_id`를 붙입니다. DB에 쓰지 않습니다.
"""
import os
import threading
from datetime import timedelta

import requests

import chatbot

AURA_API = "https://api.neo4j.io"
DEFAULT_SESSION_NAME = "arxiv_graphrag"
SESSION_TTL = timedelta(minutes=30)   # 이 시간 동안 놀면 세션이 스스로 내려갑니다.
# Free 등급 한도입니다. 이 한도를 넘는 그래프는 유료 등급이라야 세션을 띄울 수 있습니다.
FREE_TIER = {"memory": "2GB", "sessions": 1, "max_hours": 4}

# 세션·투영은 프로세스 안에서 재사용합니다. 두 세션이 동시에 붙을 수 있어 잠금으로 감쌉니다.
gds_lock = threading.RLock()
gds_cache = {}


def api_credentials():
    """Aura API 자격증명을 만듭니다. project_id는 env 값이 없으면 API로 찾습니다."""
    from graphdatascience.session import AuraAPICredentials

    client_id = chatbot.env("CLIENT_ID")
    client_secret = os.getenv("CLIENT_SECRET") or ""
    if not client_id or not client_secret:
        raise ValueError(f"{chatbot.root_dir / '.env'} 에 CLIENT_ID·CLIENT_SECRET이 없습니다. "
                         "Aura 콘솔의 API 자격증명을 넣으세요.")
    project_id = chatbot.env("AURA_PROJECT_ID", "AURA_TENANT_ID") or resolve_project(
        client_id, client_secret)
    return AuraAPICredentials(client_id=client_id, client_secret=client_secret,
                              project_id=project_id)


def resolve_project(client_id, client_secret):
    """프로젝트(테넌트)가 하나면 그것을 쓰고, 여럿이면 env로 지정하게 합니다."""
    with gds_lock:
        if "project_id" in gds_cache:
            return gds_cache["project_id"]
        token = requests.post(f"{AURA_API}/oauth/token", auth=(client_id, client_secret),
                              data={"grant_type": "client_credentials"}, timeout=30)
        token.raise_for_status()
        head = {"Authorization": f"Bearer {token.json()['access_token']}"}
        tenants = requests.get(f"{AURA_API}/v1/tenants", headers=head, timeout=30)
        tenants.raise_for_status()
        rows = tenants.json().get("data", [])
        if len(rows) != 1:
            names = [row.get("name") for row in rows]
            raise ValueError(f"Aura 프로젝트가 {len(rows)}개입니다({names}). "
                             ".env에 AURA_PROJECT_ID로 하나를 지정하세요.")
        gds_cache["project_id"] = rows[0]["id"]
        return gds_cache["project_id"]


def sessions_client():
    """세션 목록·생성에 쓰는 클라이언트입니다. 이것만으로는 아무것도 만들지 않습니다."""
    from graphdatascience.session import GdsSessions

    with gds_lock:
        if "sessions" not in gds_cache:
            gds_cache["sessions"] = GdsSessions(api_credentials=api_credentials())
        return gds_cache["sessions"]


def list_sessions():
    """떠 있는 세션을 화면에 보여 줄 형태로 돌려줍니다. 조회만 합니다."""
    return [{"name": info.name, "memory": info.memory.value, "status": info.status,
             "expiry": info.expiry_date, "created": info.created_at}
            for info in sessions_client().list()]


GRAPH_SIZE = """
MATCH (p:ArxivPaper)
RETURN count(p) AS nodes, COUNT { ()-[:REFERENCES]->() } AS relationships
"""


def graph_size():
    """인용망 크기를 Cypher로 셉니다. GDS 경로는 직접 계산용 행렬을 만들지 않습니다."""
    with gds_lock:
        if "size" not in gds_cache:
            gds_cache["size"] = chatbot.run_read(GRAPH_SIZE)[0]
        return gds_cache["size"]


def category_count(code, include_secondary=True):
    """분류에 해당하는 논문 수를 Cypher로 셉니다."""
    condition = "$code IN p.categories" if include_secondary else "p.primary_category = $code"
    return chatbot.run_read(f"MATCH (p:ArxivPaper) WHERE {condition} RETURN count(p) AS n",
                            {"code": code})[0]["n"]


def estimated_memory():
    """이 인용망에 필요한 세션 메모리 등급을 Aura에 물어봅니다. 조회만 합니다."""
    size = graph_size()
    return sessions_client().estimate(node_count=size["nodes"],
                                      relationship_count=size["relationships"])


def instance_tier():
    """이 인스턴스의 Aura 등급을 조회합니다. free-db면 세션이 청구되지 않습니다."""
    with gds_lock:
        if "tier" in gds_cache:
            return gds_cache["tier"]
        client_id = chatbot.env("CLIENT_ID")
        secret = os.getenv("CLIENT_SECRET") or ""
        token = requests.post(f"{AURA_API}/oauth/token", auth=(client_id, secret),
                              data={"grant_type": "client_credentials"}, timeout=30)
        token.raise_for_status()
        head = {"Authorization": f"Bearer {token.json()['access_token']}"}
        listing = requests.get(f"{AURA_API}/v1/instances", headers=head, timeout=30)
        listing.raise_for_status()
        # 앱이 붙어 있는 인스턴스를 연결 URI의 호스트 이름으로 찾습니다.
        host = chatbot.neo4j_uri.split("//")[-1].split(":")[0]
        instance_id = host.split(".")[0]
        match = next((row for row in listing.json().get("data", [])
                      if row.get("id") == instance_id), None)
        if match is None:
            gds_cache["tier"] = {"type": None, "free": False, "name": None}
            return gds_cache["tier"]
        detail = requests.get(f"{AURA_API}/v1/instances/{instance_id}", headers=head, timeout=30)
        kind = detail.json().get("data", {}).get("type") if detail.ok else match.get("type")
        gds_cache["tier"] = {"type": kind, "free": kind == "free-db", "name": match.get("name")}
        return gds_cache["tier"]


def connect(session_name=DEFAULT_SESSION_NAME, memory=None, create=False):
    """세션에 붙습니다. create=False면 이미 Ready인 세션에만 붙고 새로 만들지 않습니다."""
    from graphdatascience.session import DbmsConnectionInfo

    with gds_lock:
        if gds_cache.get("session_name") == session_name and "gds" in gds_cache:
            return gds_cache["gds"]
        ready = {info["name"] for info in list_sessions() if info["status"] == "Ready"}
        if session_name not in ready and not create:
            raise LookupError(f"'{session_name}' 세션이 떠 있지 않습니다. "
                              "이미 떠 있는 세션을 고르거나, 새 세션 만들기를 눌러 주세요.")
        # 같은 이름의 세션이 이미 있으면 붙기만 하고, 없을 때만 새로 만듭니다.
        gds = sessions_client().get_or_create(
            session_name=session_name,
            memory=memory or estimated_memory(),
            db_connection=DbmsConnectionInfo(chatbot.neo4j_uri, chatbot.neo4j_user,
                                             chatbot.neo4j_password),
            ttl=SESSION_TTL,
        )
        gds_cache.update({"gds": gds, "session_name": session_name, "graphs": {}})
        return gds


def graph_name(code, undirected):
    """GDS 그래프 이름에는 점을 쓸 수 없어 밑줄로 바꿉니다."""
    scope = (code or "all").replace(".", "_").replace("-", "_")
    return f"arxiv_pr_{scope}_{'undirected' if undirected else 'directed'}"


def projection(gds, code=None, include_secondary=True, undirected=False):
    """인용망을 세션으로 원격 투영합니다. 같은 조건은 한 번만 투영하고 재사용합니다."""
    name = graph_name(code, undirected)
    with gds_lock:
        graphs = gds_cache.setdefault("graphs", {})
        if name in graphs:
            return graphs[name]
        if code is None:
            where = ""
        elif include_secondary:
            # 값은 코드 목록에서 고른 것이라 임의 문자열이 아닙니다.
            where = f'WHERE "{code}" IN s.categories AND "{code}" IN t.categories'
        else:
            where = f'WHERE s.primary_category = "{code}" AND t.primary_category = "{code}"'
        graph, _ = gds.graph.project(
            name,
            f"MATCH (s:ArxivPaper)-[:REFERENCES]->(t:ArxivPaper) {where} "
            "RETURN gds.graph.project.remote(s, t)",
        )
        if undirected:
            # 개인화 PageRank는 인용한 쪽·인용된 쪽을 모두 이웃으로 봐야 합니다.
            gds.graph.relationships.toUndirected(
                graph, relationshipType="__ALL__", mutateRelationshipType="RELATED")
        graphs[name] = graph
        return graph


LOOKUP_CHUNK = 1000


def attach_ids(frame, limit, code=None, include_secondary=True, exclude=()):
    """GDS의 nodeId를 AuraDB에서 arxiv_id·제목으로 되읽습니다. 조회 전용입니다.

    분류 조건도 여기서 겁니다. 점수 높은 순으로 1000개씩 끊어 확인하므로,
    상위권에 그 분류 논문이 드물어도 limit만큼 채울 때까지 따라 내려갑니다.
    """
    condition = ""
    if code is not None:
        condition = (" AND $code IN p.categories" if include_secondary
                     else " AND p.primary_category = $code")
    cypher = f"""
    MATCH (p:ArxivPaper)
    WHERE id(p) IN $ids{condition}
    RETURN id(p) AS node_id, p.arxiv_id AS arxiv_id, p.title AS title,
           p.primary_category AS primary_category, p.categories AS categories,
           p.pdf_url AS pdf_url, toString(p.published) AS published,
           COUNT {{ (p)<-[:REFERENCES]-() }} AS cited_by
    """
    top = frame.sort_values("score", ascending=False)
    top = top[top["score"] > 0]
    ranked = []
    for start in range(0, len(top), LOOKUP_CHUNK):
        chunk = top.iloc[start:start + LOOKUP_CHUNK]
        rows = chatbot.run_read(cypher, {"ids": [int(v) for v in chunk["nodeId"]], "code": code})
        detail = {row["node_id"]: row for row in rows}
        for node_id, score in zip(chunk["nodeId"], chunk["score"]):
            row = detail.get(int(node_id))
            if row is None or row["arxiv_id"] in exclude:
                continue
            ranked.append({"rank": len(ranked) + 1, "score": float(score),
                           **{k: v for k, v in row.items() if k != "node_id"}})
            if len(ranked) == limit:
                return ranked
    return ranked


def category_pagerank(code, limit=20, include_secondary=True, scope="global",
                      session_name=DEFAULT_SESSION_NAME):
    """chatbot.category_pagerank와 같은 모양의 결과를 GDS 세션에서 계산합니다."""
    gds = connect(session_name)
    # global은 인용망 전체를 투영한 뒤 분류로 거르고, subgraph는 투영 단계에서 거릅니다.
    graph = projection(gds, None if scope == "global" else code, include_secondary)
    frame = gds.pageRank.stream(graph, dampingFactor=chatbot.DAMPING,
                                maxIterations=chatbot.PAGERANK_ITERATIONS)
    ranked = attach_ids(frame, limit, code, include_secondary)
    return {"code": code, "scope": scope, "include_secondary": include_secondary,
            "paper_count": category_count(code, include_secondary), "papers": ranked,
            "engine": "gds", "session_name": session_name,
            "projected_nodes": int(graph.node_count()),
            "projected_relationships": int(graph.relationship_count())}


def drop_graphs():
    """투영한 그래프만 지웁니다. 세션은 그대로 두고 TTL로 내려가게 합니다."""
    with gds_lock:
        for graph in gds_cache.pop("graphs", {}).values():
            try:
                graph.drop()
            except Exception:
                pass  # 세션이 이미 내려갔으면 지울 것도 없습니다.
        gds_cache["graphs"] = {}
