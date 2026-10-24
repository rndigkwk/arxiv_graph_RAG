"""세션에 저장한 논문 목록을 다루는 작은 순수 함수 모음."""

MAX_SAVED_PAPERS = 20


def add_paper(saved, paper):
    """arXiv ID 기준으로 중복을 합치고 초록 등 기존 정보를 보존합니다."""
    paper_id = paper.get("arxiv_id")
    if not isinstance(paper_id, str) or not paper_id.strip():
        return list(saved)
    items = [dict(item) for item in saved]
    for position, current in enumerate(items):
        if current.get("arxiv_id") == paper_id:
            items[position] = {**current, **{k: v for k, v in paper.items() if v not in (None, "")}}
            return items
    if len(items) >= MAX_SAVED_PAPERS:
        return items
    items.append(dict(paper))
    return items


def remove_paper(saved, paper_id):
    """지정한 arXiv ID를 목록에서 제거합니다."""
    return [dict(item) for item in saved if item.get("arxiv_id") != paper_id]


def compare_selection(saved, selected_ids):
    """저장 논문 중 초록이 있는 2~4편을 선택 순서대로 반환합니다."""
    selected_ids = list(dict.fromkeys(selected_ids))
    if len(selected_ids) < 2:
        return [], "비교할 논문을 2~4편 선택하세요."
    if len(selected_ids) > 4:
        return [], "한 번에 최대 4편까지 비교할 수 있습니다."
    by_id = {item.get("arxiv_id"): item for item in saved}
    selected = [by_id[paper_id] for paper_id in selected_ids if paper_id in by_id]
    if len(selected) != len(selected_ids):
        return [], "저장 목록에서 논문을 찾지 못했습니다. 목록을 새로고침하세요."
    if any(not isinstance(item.get("abstract"), str) or not item["abstract"].strip()
           for item in selected):
        return [], "선택한 논문 중 초록이 없는 논문이 있습니다."
    return selected, ""
