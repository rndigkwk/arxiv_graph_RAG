from streamlit.testing.v1 import AppTest

import app
from paper_library import add_paper, compare_selection, remove_paper


def paper(paper_id, **values):
    return {"arxiv_id": paper_id, "title": f"Title {paper_id}", **values}


def test_add_paper_deduplicates_by_id_and_keeps_existing_abstract():
    saved = [paper("a", abstract="long abstract")]

    updated = add_paper(saved, paper("a", title="Updated title"))

    assert updated == [paper("a", title="Updated title", abstract="long abstract")]


def test_add_paper_caps_saved_list_at_twenty():
    saved = [paper(str(i)) for i in range(20)]

    updated = add_paper(saved, paper("new"))

    assert len(updated) == 20
    assert all(item["arxiv_id"] != "new" for item in updated)


def test_remove_paper_removes_only_requested_id():
    assert remove_paper([paper("a"), paper("b")], "a") == [paper("b")]


def test_compare_selection_requires_two_to_four_papers_with_abstracts():
    saved = [paper(pid, abstract="Abstract") for pid in "abcde"]
    saved[-1].pop("abstract")

    assert compare_selection(saved, ["a"]) == ([], "비교할 논문을 2~4편 선택하세요.")
    assert compare_selection(saved, list("abcde"))[1] == "한 번에 최대 4편까지 비교할 수 있습니다."
    assert compare_selection(saved, ["a", "e"])[1] == "선택한 논문 중 초록이 없는 논문이 있습니다."
    assert [item["arxiv_id"] for item in compare_selection(saved, ["c", "a"])[0]] == ["c", "a"]


def test_saved_papers_ui_compares_selected_session_papers():
    def page(app_module):
        import streamlit as st

        st.session_state.saved_papers = [
            {"arxiv_id": pid, "title": f"Title {pid}", "abstract": f"Abstract {pid}"}
            for pid in "abc"
        ]

        class FakeChatbot:
            def compare_papers(self, selected):
                return {"papers": [{
                    "arxiv_id": item["arxiv_id"],
                    **{
                        field: {
                            "text": f"{field} {item['arxiv_id']}",
                            "evidence_ids": [item["arxiv_id"]],
                        }
                        for field in (
                            "research_question", "method", "data_and_evaluation", "key_findings"
                        )
                    },
                } for item in selected]}

        app_module.show_saved_papers(FakeChatbot())

    at = AppTest.from_function(page, args=(app,)).run()
    assert at.exception == []
    at.multiselect[1].set_value(["c", "a"]).run()
    at.button[2].click().run()

    assert len(at.dataframe) == 2
    assert at.dataframe[1].value["arXiv ID"].tolist() == ["c", "a"]


def test_save_paper_updates_session_without_duplicate_ids():
    def page(app_module):
        import streamlit as st

        st.session_state.saved_papers = [
            {"arxiv_id": "a", "title": "Title a", "abstract": "old"}
        ]
        app_module._save_paper({"arxiv_id": "a", "title": "New title"})
        st.write(st.session_state.saved_papers)

    at = AppTest.from_function(page, args=(app,)).run()

    assert at.session_state["saved_papers"] == [
        paper("a", title="New title", abstract="old")
    ]
