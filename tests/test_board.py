"""Board tests with a fake `gh` runner: exact queries, typed variables, the R9 card sequence, and failure isolation."""
from __future__ import annotations

import dataclasses
import json

import pytest

from fridica_research import board
from fridica_research.board import Board, Projects
from fridica_research.config import Board as BoardCfg
from support import CFG, World

BCFG = dataclasses.replace(CFG, board=BoardCfg(enabled=True, owner="chengcli", number=8, repo="chengcli/fridica-research"))
PROJECT = {"id": "PVT_1", "fields": {"nodes": [
    {"id": "F_stage", "name": "Stage", "dataType": "SINGLE_SELECT", "options": [{"id": f"O_{n}", "name": n} for n in board.STAGE_OPTIONS]},
    *[{"id": f"F_{n.replace(' ', '_')}", "name": n, "dataType": k} for n, k in board.FIELDS.items() if n != "Stage"],
]}}


class FakeGh:
    """Answers gh calls; records (argv, parsed stdin). Missing fields and failures are scriptable."""

    def __init__(self, fields_missing: tuple[str, ...] = (), fail_on: str | None = None):
        self.calls: list[tuple[list[str], dict | None]] = []
        self.issues = 0
        self.missing = set(fields_missing)
        self.fail_on = fail_on

    def __call__(self, argv: list[str], stdin: str | None) -> str:
        doc = json.loads(stdin) if stdin else None
        self.calls.append((argv, doc))
        if self.fail_on and self.fail_on in " ".join(argv) + (doc or {}).get("query", ""): raise RuntimeError("gh failed")
        if argv[:3] == ["gh", "api", "graphql"]:
            return json.dumps({"data": self.answer(doc["query"], doc["variables"])})
        if argv[:3] == ["gh", "issue", "create"]:
            self.issues += 1
            return f"https://github.com/{argv[4]}/issues/{self.issues}\n"
        if argv[:3] == ["gh", "project", "field-create"]:
            self.missing.discard(argv[argv.index("--name") + 1])
            return "{}"
        return ""

    def answer(self, query: str, v: dict) -> dict:
        if query == board.Q_DISCOVER["user"]:
            nodes = [f for f in PROJECT["fields"]["nodes"] if f["name"] not in self.missing]
            return {"user": {"projectV2": {"id": PROJECT["id"], "fields": {"nodes": nodes}}}}
        if query == board.Q_ISSUE: return {"repository": {"issue": {"id": f"I_{v['number']}"}}}
        if query == board.M_ADD_ITEM: return {"addProjectV2ItemById": {"item": {"id": "PVTI_" + v["content"]}}}
        if query == board.M_SUB_ISSUE: return {"addSubIssue": {"issue": {"id": v["parent"]}}}
        if query in (board.M_SET_DATE, board.M_SET_NUMBER, board.M_SET_TEXT, board.M_SET_OPTION): return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": v["item"]}}}
        if query == board.Q_ITEMS["user"]: return {"user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"id": "PVTI_I_3", "content": {"number": 3, "repository": {"name": "fridica-research"}}}]}}}}
        if query == board.Q_VERIFY: return {"repository": {"issue": {"projectItems": {"nodes": [{"project": {"number": 8}, "started": {"date": "2026-10-04"}, "projected_finish": {"date": "2026-10-04"}, "finished": None, "projected_hours": {"number": 4.0}, "actual_hours": None, "stage": {"name": "Explore"}}]}}}}
        raise AssertionError(f"unexpected query {query[:60]}")

    def sets(self, mutation: str) -> list[dict]: return [doc["variables"] for argv, doc in self.calls if doc and doc["query"] == mutation]
    def argv(self, *prefix: str) -> list[list[str]]: return [a for a, _ in self.calls if tuple(a[: len(prefix)]) == prefix]


def make(gh: FakeGh | None = None, cfg=BCFG):
    gh = gh or FakeGh()
    meta: dict[str, str] = {}
    b = Board(cfg, runner=gh, remember=lambda k, v: meta.__setitem__(k, v) if v is not None else meta.pop(k, None), recall=meta.get)
    return b, gh, meta


def test_discover_is_one_query_and_cached():
    b, gh, meta = make()
    ids = b.api.discover("chengcli", 8)
    assert gh.calls[0][0] == ["gh", "api", "graphql", "--input", "-"]
    assert gh.calls[0][1] == {"query": board.Q_DISCOVER["user"], "variables": {"owner": "chengcli", "number": 8}}
    assert ids.id == "PVT_1" and ids.fields["Stage"]["options"]["Explore"] == "O_Explore" and ids.fields["Started"]["dataType"] == "DATE"
    assert "board:project:chengcli/8" in meta
    b.api.discover("chengcli", 8)
    assert len(gh.calls) == 1


def test_typed_field_writes_and_item_lookup():
    b, gh, meta = make()
    b.api.set_dates(3, started="2026-10-04", projected_finish="2026-10-05")
    b.api.set_number(3, "Actual hours", 0.17)
    b.api.set_text(3, "Thread", "T1:C1:1")
    b.api.set_option(3, "Stage", "Debate")
    items = [d for _, d in gh.calls if d and d["query"] == board.Q_ITEMS["user"]]
    assert len(items) == 1 and items[0]["variables"] == {"owner": "chengcli", "number": 8, "after": None}  # cached after the first lookup
    assert gh.sets(board.M_SET_DATE) == [{"project": "PVT_1", "item": "PVTI_I_3", "field": "F_Started", "date": "2026-10-04"}, {"project": "PVT_1", "item": "PVTI_I_3", "field": "F_Projected_finish", "date": "2026-10-05"}]
    assert gh.sets(board.M_SET_NUMBER) == [{"project": "PVT_1", "item": "PVTI_I_3", "field": "F_Actual_hours", "v": 0.17}]
    assert isinstance(gh.sets(board.M_SET_NUMBER)[0]["v"], float)
    assert "$v:Float!" in board.M_SET_NUMBER and "$date:Date!" in board.M_SET_DATE
    assert gh.sets(board.M_SET_TEXT) == [{"project": "PVT_1", "item": "PVTI_I_3", "field": "F_Thread", "v": "T1:C1:1"}]
    assert gh.sets(board.M_SET_OPTION) == [{"project": "PVT_1", "item": "PVTI_I_3", "field": "F_stage", "v": "O_Debate"}]
    assert meta["board:item:chengcli/fridica-research#3"] == "PVTI_I_3"


def test_verify_reads_field_values_by_name():
    b, gh, _ = make()
    v = b.api.verify(3)
    assert gh.calls[-1][1]["variables"] == {"owner": "chengcli", "name": "fridica-research", "number": 3}
    assert v == {"started": "2026-10-04", "projected_finish": "2026-10-04", "finished": None, "projected_hours": 4.0, "actual_hours": None, "stage": "Explore"}


def test_missing_fields_are_created_once():
    gh = FakeGh(fields_missing=("Peer reviewers", "Finished"))
    b, gh, _ = make(gh)
    b.api.ensure_fields()
    creates = gh.argv("gh", "project", "field-create")
    assert [c[c.index("--name") + 1] for c in creates] == ["Finished", "Peer reviewers"]
    assert creates[0][creates[0].index("--data-type") + 1] == "DATE"
    assert "Peer reviewers" in b.api.project().fields
    b.api.ensure_fields()
    assert len(gh.argv("gh", "project", "field-create")) == 2


def test_study_and_stage_cards_follow_the_bootstrap_sequence():
    """R9: parent issue #1, then stage sub-issues #2.. as stages start; closed with Finished and Actual hours."""
    w = World(cfg=BCFG)
    b, gh, meta = make()
    w.start()
    b.sync(w.state)
    creates = gh.argv("gh", "issue", "create")
    assert len(creates) == 2 and creates[0][6] == "Study the thing" and creates[1][6].startswith("Explore (iteration 1)")
    assert creates[0][creates[0].index("--assignee") + 1] == "chengcli"
    assert gh.sets(board.M_SUB_ISSUE) == [{"parent": "I_1", "child": "I_2"}]
    assert [v["content"] for v in gh.sets(board.M_ADD_ITEM)] == ["I_1", "I_2"]
    dates = {(v["item"], v["field"]): v["date"] for v in gh.sets(board.M_SET_DATE)}
    assert dates[("PVTI_I_1", "F_Started")] == "2023-11-14" and ("PVTI_I_1", "F_Projected_finish") in dates and ("PVTI_I_2", "F_Started") in dates
    w.to_delivered()
    b.sync(w.state)
    cards = json.loads(meta[f"board:{w.state.thread}"])
    assert cards["issue"] == 1 and [c["issue"] for c in cards["stages"]] == [2, 3, 4, 5, 6, 7] and all(c["closed"] for c in cards["stages"])
    titles = [a[6].split(":")[0] for a in gh.argv("gh", "issue", "create")[1:]]
    assert titles == [f"{s} (iteration 1)" for s in ("Explore", "Claim", "Debate", "Implement", "Audit", "Deliver")]
    closed = [a[3] for a in gh.argv("gh", "issue", "close")]
    assert closed == ["2", "3", "4", "5", "6", "7", "1"]
    finished = [v["item"] for v in gh.sets(board.M_SET_DATE) if v["field"] == "F_Finished"]
    assert sorted(finished) == sorted(f"PVTI_I_{n}" for n in (1, 2, 3, 4, 5, 6, 7))
    actual = [v["item"] for v in gh.sets(board.M_SET_NUMBER) if v["field"] == "F_Actual_hours"]
    assert sorted(actual) == sorted(finished)
    options = [v["v"] for v in gh.sets(board.M_SET_OPTION) if v["item"] == "PVTI_I_1"]
    assert options[0] == "O_Explore" and options[-1] == "O_Delivered"
    body_edits = [a for a in gh.argv("gh", "issue", "edit") if "--body" in a]
    assert "| Stage | Start | Projected | End | Actual |" in body_edits[-1][body_edits[-1].index("--body") + 1]
    assert any(v["field"] == "F_Peer_reviewers" and v["v"] == "reviewer" for v in gh.sets(board.M_SET_TEXT))


def test_existing_issue_is_attached_not_created():
    w = World(cfg=BCFG)
    b, gh, meta = make()
    b.issue_numbers = {w.start().thread: 42}
    b.sync(w.state)
    assert [a[6] for a in gh.argv("gh", "issue", "create")] == ["Explore (iteration 1): Study the thing"]
    assert gh.sets(board.M_ADD_ITEM)[0]["content"] == "I_42" and gh.sets(board.M_SUB_ISSUE) == [{"parent": "I_42", "child": "I_1"}]
    assert json.loads(meta[f"board:{w.state.thread}"])["issue"] == 42


def test_board_failure_is_logged_and_retried_next_transition(caplog):
    w = World(cfg=BCFG)
    gh = FakeGh(fail_on="addSubIssue")
    b, gh, meta = make(gh)
    w.start()
    b.sync(w.state)  # the parent card is created, the stage card's sub-issue link fails
    assert "board update failed" in caplog.text
    cards = json.loads(meta[f"board:{w.state.thread}"])
    assert cards["issue"] == 1 and cards["stages"] == []
    gh.fail_on = None
    b.sync(w.state)
    cards = json.loads(meta[f"board:{w.state.thread}"])
    assert [c["issue"] for c in cards["stages"]] == [3]  # a fresh stage issue; #2 is the orphan from the failed attempt


def test_disabled_board_does_nothing():
    w = World()
    b, gh, _ = make(cfg=CFG)
    b.sync(w.start())
    assert gh.calls == []


def test_stage_table_renders_wall_clock_rows():
    w = World()
    w.to_claim()
    t = board.stage_table(w.state)
    assert "| Explore | 2023-11-14 22:13 | 0:10 | 2023-11-14 22:13 | 0:00 |" in t and "| Claim |" in t


@pytest.mark.parametrize("name,kind", list(board.FIELDS.items()))
def test_field_catalog_matches_project_8(name, kind):
    assert kind in ("SINGLE_SELECT", "NUMBER", "DATE", "TEXT") and name != "Reviewers"


def test_projects_organization_owner_uses_organization_query():
    cfg = dataclasses.replace(BCFG, board=dataclasses.replace(BCFG.board, owner_type="organization"))
    calls = []
    def gh(argv, stdin):
        calls.append(json.loads(stdin))
        return json.dumps({"data": {"organization": {"projectV2": {"id": "PVT_o", "fields": {"nodes": []}}}}})
    assert Projects(cfg, gh).discover("org", 1).id == "PVT_o" and calls[0]["query"].startswith("query($owner:String!,$number:Int!){organization(login:$owner)")
