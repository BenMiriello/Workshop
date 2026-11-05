from services import chat_search


def _rows():
    return [
        {"id": "a", "title": "Notes about lunch"},
        {"id": "b", "title": "Queue for harbor"},
        {"id": "c", "title": "Harbor queue fixes"},
        {"id": "d", "title": "Unrelated"},
    ]


def _texts(monkeypatch, texts):
    monkeypatch.setattr(
        chat_search, "_thread_text",
        lambda tid: (texts.get(tid, ""), texts.get(tid, "").lower()),
    )


def test_title_matches_rank_above_message_matches(monkeypatch):
    _texts(monkeypatch, {"a": "we talked about the Harbor queue at length"})
    hits = chat_search.search(_rows(), "harbor queue")
    assert [h["id"] for h in hits] == ["c", "b", "a"]
    assert [h["where"] for h in hits] == ["title", "title", "content"]


def test_whole_phrase_ranks_above_scattered_words(monkeypatch):
    _texts(monkeypatch, {})
    hits = chat_search.search(_rows(), "harbor queue")
    # "Harbor queue fixes" has the phrase; "Queue for harbor" only the words.
    assert [h["id"] for h in hits] == ["c", "b"]


def test_message_match_carries_its_passage(monkeypatch):
    _texts(monkeypatch, {"d": "first line\nthe splash screen stayed up"})
    [hit] = chat_search.search(_rows(), "splash")
    assert hit["where"] == "content"
    assert "splash screen" in hit["snippet"]


def test_every_word_must_appear(monkeypatch):
    _texts(monkeypatch, {"a": "harbor only"})
    assert chat_search.search(_rows(), "harbor lunch") == []


def test_blank_query_matches_nothing(monkeypatch):
    _texts(monkeypatch, {})
    assert chat_search.search(_rows(), "  ") == []
