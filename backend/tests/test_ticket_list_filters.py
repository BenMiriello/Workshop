"""`GET /api/tasks` filtering: `status` and `state_type` as comma-separated sets.

Why these exist rather than being covered by the existing single-value tests:

  * The endpoint took ONE `status=`, and the UI's default view is "Active" =
    four statuses. Unable to say that in one request, the page fetched the whole
    cross-scope list UNPAGED (bodies, comments and evidence inline) on every
    sort, scope and search change and narrowed in the browser. So the list form
    is a performance contract, and the tests that matter are the ones about
    `total` and paging: a filter that ran after paging would give page 1 of
    everything-narrowed instead of page 1 of the filtered set, which is exactly
    the client-side behaviour being replaced.
  * `state_type` is what the UI actually means (Active / Backlog / Done). It
    expands to statuses here, through the same `tasks.state_type` map the rows
    are stamped from, so a view and its rows can never disagree.
  * An unknown value must 400 and NAME itself. Returning nothing would make a
    typo indistinguishable from an empty backlog, and the caller would read it
    as data.

Router is exercised through a bare FastAPI app (auth attaches at catalog-mount
time in main.py), matching test_ticket_state_and_triage.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import tasks  # noqa: E402


@pytest.fixture
def roots(tmp_path):
    a = tmp_path / "workshop"
    b = tmp_path / "other"
    a.mkdir()
    b.mkdir()
    return {"workshop": a, "project:other": b}


@pytest.fixture
def client(roots, monkeypatch):
    monkeypatch.setattr(tasks_all, "_root_for_scope", lambda s: roots.get(s))
    monkeypatch.setattr(tasks_all, "_scope_roots", lambda: list(roots.items()))
    monkeypatch.setattr(tasks_all, "scope_roots", lambda: list(roots.items()),
                        raising=False)
    app = FastAPI()
    app.include_router(tasks_all.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def eight(roots):
    """One ticket per status, so every filter has both a hit and a miss."""
    for status in sorted(tasks.STATUSES):
        tasks.add(roots["workshop"], title=status, status=status)
    return roots["workshop"]


def _titles(payload: dict) -> list[str]:
    return sorted(t["title"] for t in payload["items"])


# ---- single value: unchanged ------------------------------------------------

def test_one_status_behaves_exactly_as_before(client, eight):
    r = client.get("/api/tasks?status=blocked").json()
    assert _titles(r) == ["blocked"]
    assert r["page"]["total"] == 1


def test_no_status_filters_nothing(client, eight):
    assert len(client.get("/api/tasks").json()["items"]) == len(tasks.STATUSES)


def test_an_empty_status_is_not_a_filter(client, eight):
    """`?status=` (and `?status=,,`) is what a UI sends for "no filter"; it must
    not narrow to nothing."""
    assert len(client.get("/api/tasks?status=").json()["items"]) == len(tasks.STATUSES)
    assert len(client.get("/api/tasks?status=,,").json()["items"]) == len(tasks.STATUSES)


# ---- comma-separated status -------------------------------------------------

def test_status_accepts_the_active_set_in_one_request(client, eight):
    r = client.get("/api/tasks?status=todo,blocked,doing,in_review").json()
    assert _titles(r) == ["blocked", "doing", "in_review", "todo"]
    assert r["page"]["total"] == 4


def test_whitespace_around_a_comma_is_tolerated(client, eight):
    r = client.get("/api/tasks?status=todo, blocked").json()
    assert _titles(r) == ["blocked", "todo"]


def test_a_repeated_status_is_still_one_row(client, eight):
    r = client.get("/api/tasks?status=todo,todo").json()
    assert _titles(r) == ["todo"]
    assert r["page"]["total"] == 1


def test_an_unknown_status_is_a_400_that_names_it(client, eight):
    r = client.get("/api/tasks?status=todo,nonsense")
    assert r.status_code == 400
    assert "nonsense" in r.json()["detail"]


# ---- state_type -------------------------------------------------------------

def test_state_type_expands_to_its_statuses(client, eight):
    assert _titles(client.get("/api/tasks?state_type=backlog").json()) == \
        ["draft", "parked"]
    assert _titles(client.get("/api/tasks?state_type=started").json()) == \
        ["blocked", "doing", "in_review"]
    assert _titles(client.get("/api/tasks?state_type=completed").json()) == ["done"]
    assert _titles(client.get("/api/tasks?state_type=canceled").json()) == ["cancelled"]


def test_the_active_view_is_one_state_type_pair(client, eight):
    """What the UI's default view means, said in the vocabulary it means it in."""
    r = client.get("/api/tasks?state_type=unstarted,started").json()
    assert _titles(r) == ["blocked", "doing", "in_review", "todo"]
    assert r["page"]["total"] == 4


def test_state_type_agrees_with_the_state_type_on_the_rows(client, eight):
    """One map, so a filtered view can never contain a row that contradicts it."""
    for st in tasks.STATE_TYPES:
        rows = client.get(f"/api/tasks?state_type={st}").json()["items"]
        assert {row["state_type"] for row in rows} == {st}


def test_an_unknown_state_type_is_a_400_that_names_it(client, eight):
    r = client.get("/api/tasks?state_type=in_progress")
    assert r.status_code == 400
    assert "in_progress" in r.json()["detail"]


def test_a_status_value_is_not_a_state_type(client, eight):
    """The two vocabularies are separate; `cancelled` is a status and `canceled`
    is the type, one letter apart. Passing one for the other must say so."""
    assert client.get("/api/tasks?state_type=cancelled").status_code == 400
    assert client.get("/api/tasks?status=canceled").status_code == 400


# ---- composition ------------------------------------------------------------

def test_status_and_state_type_compose_with_and(client, eight):
    r = client.get("/api/tasks?state_type=started&status=blocked,done").json()
    assert _titles(r) == ["blocked"]


def test_the_filter_composes_with_scope(client, roots):
    tasks.add(roots["workshop"], title="here started", status="doing")
    tasks.add(roots["workshop"], title="here backlog", status="draft")
    tasks.add(roots["project:other"], title="there started", status="doing")
    r = client.get("/api/tasks?scope=workshop&state_type=started").json()
    assert _titles(r) == ["here started"]
    assert r["page"]["total"] == 1


def test_the_filter_composes_with_q(client, eight):
    r = client.get("/api/tasks?state_type=started&q=block").json()
    assert _titles(r) == ["blocked"]


def test_include_done_false_still_drops_terminal_rows(client, eight):
    """`include_done` and the status set are independent; the narrower wins."""
    r = client.get("/api/tasks?include_done=false&status=todo,done").json()
    assert _titles(r) == ["todo"]


# ---- the point of the whole thing: total and paging -------------------------

def test_total_is_the_size_of_the_filtered_set(client, eight):
    r = client.get("/api/tasks?state_type=unstarted,started").json()
    assert r["page"]["total"] == 4
    assert len(r["items"]) == 4
    # Not the unfiltered size, which is what a client-side narrowing reports.
    assert r["page"]["total"] != len(tasks.STATUSES)


def test_paging_pages_the_filtered_set_not_everything(client, eight):
    """The regression this replaces: the server paged the UNFILTERED list, so
    page 1 of "Active" was whatever active work fell in the first page of all
    tickets. Here every page is drawn from the four matching rows."""
    seen: list[str] = []
    for offset in (0, 2):
        page = client.get(
            f"/api/tasks?state_type=unstarted,started&sort=created&limit=2"
            f"&offset={offset}").json()
        assert page["page"]["total"] == 4
        assert len(page["items"]) == 2
        seen += [t["title"] for t in page["items"]]
    assert sorted(seen) == ["blocked", "doing", "in_review", "todo"]


def test_the_filter_runs_before_the_sort(client, eight):
    """Sorting first and filtering after would leave the same rows in a different
    order once paged; pinned by asking for a deterministic sort over a set whose
    filtered order is known."""
    r = client.get("/api/tasks?status=todo,doing,done&sort=status").json()
    # _STATUS_RANK: doing (1) < todo (3) < done (6/terminal-last).
    ranks = [tasks._STATUS_RANK.get(t["status"], 99) for t in r["items"]]
    assert ranks == sorted(ranks)
    assert [t["title"] for t in r["items"]][0] == "doing"
