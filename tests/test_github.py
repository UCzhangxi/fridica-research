"""GitHub sign-off (R21), PR hygiene (R23), driver-side merge and late reviews (R24) with a fake `gh` runner: no network."""
from __future__ import annotations

import dataclasses
import json
import os

import pytest

from fridica_research import board, config, contracts, machine, replay
from fridica_research.client import Client
from fridica_research.config import Board as BoardCfg
from fridica_research.config import GitHub as GitHubCfg
from fridica_research.config import Repo, Reviewer
from fridica_research.driver import Driver
from fridica_research.github import GitHub, PrHygieneError, parse_iso
from fridica_research.store import Store
from fake_control import FakeControl
from support import CFG, LLM, OWNER, PR, REV, SHA, World

HEAD = SHA + "0" * 33  # the PR's full head; the implementer reported the short sha
OLD = "0123456" + "f" * 33
SQUASH = "5" * 40
MERGED_AT = "2026-10-04T12:00:00Z"
GCFG = dataclasses.replace(
    CFG, audit_scopes=("scope",), require_signoffs=True, reviewers=(Reviewer(REV, "scope", "reviewer"),),
    board=BoardCfg(enabled=True, owner="chengcli", number=8, repo="o/r"),
    github=GitHubCfg(enabled=True, poll_interval=0), repos=(Repo("o/r", "driver", ("reviewer", "rev2")),),
)


def rv(rid, login, state, oid=HEAD, at="2026-10-04T10:00:00Z", body=""):
    return {"id": rid, "author": {"login": login}, "state": state, "commit": {"oid": oid}, "submittedAt": at, "body": body}


class FakeGh:
    """Answers the gh calls of github.py from a table of PR views; records (argv, parsed stdin)."""

    def __init__(self, *prs: tuple[str, dict]):
        self.prs = {contracts.pr_id(url): {"latestReviews": [], "headRefOid": HEAD, "state": "OPEN", "mergedAt": None, "mergeCommit": None, "author": {"login": "implementer"}, "milestone": None, **v} for url, v in prs}
        self.calls: list[tuple[list[str], dict | None]] = []
        self.milestones: dict[str, list[dict]] = {}

    def __call__(self, argv: list[str], stdin: str | None) -> str:
        doc = json.loads(stdin) if stdin else None
        self.calls.append((argv, doc))
        if argv[:3] == ["gh", "pr", "view"]: return json.dumps(self.prs[(argv[argv.index("-R") + 1], argv[3])])
        if argv[:3] == ["gh", "pr", "merge"]:
            self.prs[(argv[argv.index("-R") + 1], argv[3])].update(state="MERGED", mergedAt=MERGED_AT, mergeCommit={"oid": SQUASH})
            return ""
        if argv[:3] == ["gh", "pr", "create"]: return f"https://github.com/{argv[argv.index('-R') + 1]}/pull/13\n"
        if argv[:3] == ["gh", "api", "graphql"]:
            q, v = doc["query"], doc["variables"]
            if q == board.Q_DISCOVER["user"]: return json.dumps({"data": {"user": {"projectV2": {"id": "PVT_1", "fields": {"nodes": []}}}}})
            if "pullRequest(number" in q: return json.dumps({"data": {"repository": {"pullRequest": {"id": f"PR_{v['name']}_{v['number']}"}}}})
            if q == board.M_ADD_ITEM: return json.dumps({"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_" + v["content"]}}}})
            raise AssertionError(q[:60])
        if argv[:2] == ["gh", "api"]:
            path = next(a for a in argv[2:] if a.startswith("repos/"))
            if path.endswith("/milestones") and "POST" in argv:
                ms = self.milestones.setdefault(path, [])
                ms.append({"title": doc["title"], "number": len(ms) + 1})
                return json.dumps(ms[-1])
            if "/milestones?" in path: return json.dumps(self.milestones.get(path.split("?")[0], []))
            return "{}"
        raise AssertionError(argv)

    def argv(self, *prefix: str) -> list[list[str]]: return [a for a, _ in self.calls if tuple(a[: len(prefix)]) == prefix]
    def api(self, path: str) -> list[tuple[list[str], dict | None]]: return [(a, d) for a, d in self.calls if a[:2] == ["gh", "api"] and path in a]


def make(gh: FakeGh, cfg=GCFG):
    meta: dict[str, str] = {}
    return GitHub(cfg, runner=gh, remember=lambda k, v: meta.__setitem__(k, v) if v is not None else meta.pop(k, None), recall=meta.get), meta


def audit_world(cfg=GCFG, pr=PR) -> World:
    w = World(cfg=cfg)
    w.to_audit(pr=pr)
    assert w.state.stage == "Audit" and w.state.phase == "signoff"
    return w


def poll(gh_client: GitHub, w: World, now: float | None = None):
    out = gh_client.poll(w.state, now if now is not None else w.now)
    for ev in out.events: w.feed(ev)
    return out


# -- R21: reviews as sign-offs -----------------------------------------------------------
def test_review_requests_go_through_rest_once_per_head_never_to_the_author():
    gh = FakeGh((PR, {"author": {"login": "rev2"}}))
    g, _ = make(gh)
    w = audit_world()
    poll(g, w)
    poll(g, w)
    reqs = gh.api("repos/o/r/pulls/9/requested_reviewers")
    assert reqs == [(["gh", "api", "-X", "POST", "repos/o/r/pulls/9/requested_reviewers", "--input", "-"], {"reviewers": ["reviewer"]})]
    assert not gh.argv("gh", "pr", "edit")
    gh.prs[("o/r", "9")]["headRefOid"] = OLD  # a new push: requested again on the new head
    poll(g, w)
    assert len(gh.api("repos/o/r/pulls/9/requested_reviewers")) == 2


def test_copilot_approved_is_ignored():
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "copilot-pull-request-reviewer", "APPROVED"), rv("R2", "dependabot[bot]", "APPROVED")]}))
    g, _ = make(gh)
    w = audit_world()
    out = poll(g, w)
    assert out.events == [] and out.posts == [] and not gh.argv("gh", "pr", "merge")
    assert w.state.stage == "Audit" and w.state.audit_scopes["scope"]["signed_at"] is None


def test_approval_on_an_old_head_is_ignored():
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "APPROVED", oid=OLD)]}))
    g, _ = make(gh)
    w = audit_world()
    out = poll(g, w)
    assert out.events == [] and out.posts == [] and not gh.argv("gh", "pr", "merge")
    assert w.state.stage == "Audit" and w.state.signoffs == {}


def test_approval_on_the_head_closes_the_card_and_merges_with_the_squash_sha():
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "Reviewer", "APPROVED")]}))
    g, meta = make(gh)
    w = audit_world()
    out = poll(g, w)
    assert [e.kind for e in out.events] == ["sign_off"] and out.events[0].data == {"sender": REV, "pr": PR, "sha": HEAD, "verdict": "approve"}  # login -> Slack id, case-insensitive
    assert w.state.stage == "Delivered" and w.state.audit_scopes["scope"]["verdict"] == "approve"  # full head vs the implementer's short sha
    assert gh.argv("gh", "pr", "merge") == [["gh", "pr", "merge", "9", "-R", "o/r", "--squash", "--match-head-commit", HEAD]]
    assert meta["github:revision:o/r:g1"] == SQUASH and json.loads(meta[f"github:pr:{w.state.thread}:o/r#9"])["merged_sha"] == SQUASH
    assert out.posts[-1][1] == f"merged o/r#9 (squash) as {SQUASH} after approval by Reviewer on {HEAD[:12]}"
    poll(g, w)
    assert len(gh.argv("gh", "pr", "merge")) == 1  # merged once


def test_merge_only_for_driver_repos_and_only_without_changes_on_the_head():
    owner_cfg = dataclasses.replace(GCFG, repos=(Repo("o/r", "owner"),))
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "APPROVED")]}))
    g, _ = make(gh, owner_cfg)
    w = audit_world(owner_cfg)
    out = poll(g, w)
    poll(g, w)
    assert not gh.argv("gh", "pr", "merge") and [p for _, p in out.posts if "merged by its owner" in p] == [f"{PR} approved on {HEAD[:12]} by reviewer; o/r is merged by its owner"]
    unlisted = dataclasses.replace(GCFG, repos=())  # a repository not in [repos] is the owner's
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "APPROVED")]}))
    g, _ = make(gh, unlisted)
    poll(g, audit_world(unlisted))
    assert not gh.argv("gh", "pr", "merge")
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "APPROVED"), rv("R2", "rev2", "CHANGES_REQUESTED")]}))
    g, _ = make(gh)
    poll(g, audit_world())
    assert not gh.argv("gh", "pr", "merge")
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "COMMENTED"), rv("R2", "copilot", "APPROVED")]}))  # no non-bot approval
    g, _ = make(gh)
    poll(g, audit_world())
    assert not gh.argv("gh", "pr", "merge")


def test_commented_is_a_finding_without_verdict():
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "COMMENTED", body="nit: rename x")]}))
    g, _ = make(gh)
    w = audit_world()
    out = poll(g, w)
    assert [e.kind for e in out.events] == ["finding"]
    assert w.state.findings[-1] == f"iteration 1 note during Audit: GitHub review by reviewer on o/r#9 at {HEAD[:12]} (COMMENTED, no verdict): nit: rename x"
    assert w.state.signoffs == {} and w.state.audit_scopes["scope"]["signed_at"] is None and w.state.stage == "Audit"


def test_changes_requested_returns_the_study():
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "CHANGES_REQUESTED")]}))
    g, _ = make(gh)
    w = audit_world()
    poll(g, w)
    assert w.state.iteration == 2 and w.state.audit["verdict"] == "return"


def test_one_mirror_line_per_review_never_parsed_back():
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "COMMENTED")]}))
    g, _ = make(gh)
    w = audit_world()
    first = poll(g, w)
    gh.prs[("o/r", "9")]["latestReviews"].append(rv("R2", "rev2", "APPROVED"))
    second = poll(g, w)
    third = poll(g, w)
    lines = [t for _, t in first.posts + second.posts + third.posts if t.startswith("SIGN-OFF")]
    assert lines == [f"SIGN-OFF {contracts.MIRROR_MARK} reviewer: COMMENTED on o/r#9 at {HEAD[:12]}", f"SIGN-OFF {contracts.MIRROR_MARK} rev2: APPROVED on o/r#9 at {HEAD[:12]}"]
    for line in lines:
        assert contracts.parse_signoff(line) is None and "sign_off" not in replay.contract_lines(line)
    assert contracts.parse_signoff(f"SIGN-OFF {contracts.MIRROR_MARK} x\nSIGN-OFF {PR} {SHA} approve") is None  # the whole mirror post is never a sign-off


def test_driver_mirrors_into_the_thread_and_the_echo_is_not_a_signoff(tmp_path, sock_dir):
    sock = os.path.join(sock_dir, "c.sock")
    server = FakeControl(sock, owner=OWNER).start()
    try:
        cfg = dataclasses.replace(GCFG, socket=sock, state_path=str(tmp_path / "s.sqlite3"))
        w = audit_world(cfg)
        store = Store(cfg.state_file)
        store.save(w.state, 0, w.now)
        gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "COMMENTED", body="looks fine")]}))
        g, _ = make(gh, cfg)
        drv = Driver(cfg, Client(cfg.socket_path), store, llm=lambda n, p: LLM[n], clock=lambda: w.now, sleep=lambda s: None, github=g)
        for _ in range(3): drv.run_once()
        posts = [m for m in server.view(w.state.thread)["messages"] if contracts.MIRROR_MARK in m["text"]]
        assert len(posts) == 1 and posts[0]["text"].endswith(f"ref: {w.state.thread}/github/review-R1")
        s = store.load(w.state.thread)
        assert s.signoffs == {} and s.stage == "Audit" and sum("COMMENTED" in f for f in s.findings) == 1
    finally:
        server.stop()


def test_github_disabled_polls_nothing():
    gh = FakeGh((PR, {"latestReviews": [rv("R1", "reviewer", "APPROVED")]}))
    g, _ = make(gh, dataclasses.replace(GCFG, github=GitHubCfg()))
    assert g.poll(audit_world().state, 0).events == [] and gh.calls == []


# -- R24: late reviews -------------------------------------------------------------------
def test_post_merge_changes_requested_is_acknowledged_recorded_and_carried():
    late = rv("R9", "reviewer", "CHANGES_REQUESTED", at="2026-10-04T13:00:00Z", body="- rename foo\n- add a test for bar\n")
    gh = FakeGh((PR, {"state": "MERGED", "mergedAt": MERGED_AT, "mergeCommit": {"oid": SQUASH}, "latestReviews": [rv("R1", "rev2", "APPROVED", at="2026-10-04T11:00:00Z")]}))
    g, meta = make(gh)
    w = audit_world()
    w.ev("sign_off", sender=REV, pr=PR, sha=SHA, verdict="approve")  # the study delivered on the Slack fallback
    assert w.state.stage == "Delivered"
    poll(g, w)
    gh.prs[("o/r", "9")]["latestReviews"].append(late)
    out = poll(g, w)
    assert gh.api("repos/o/r/issues/9/comments") == [(["gh", "api", "-X", "POST", "repos/o/r/issues/9/comments", "--input", "-"], {"body": "@reviewer acknowledged, goes into the next PR."})]
    assert ("ack-R9", "acknowledged, goes into the next PR: post-merge review by reviewer on o/r#9") in out.posts
    assert any(t.endswith("(after merge)") for _, t in out.posts)
    assert w.state.findings[-2:] == ["iteration 1 note during Delivered: post-merge review by reviewer on o/r#9: rename foo", "iteration 1 note during Delivered: post-merge review by reviewer on o/r#9: add a test for bar"]
    assert w.state.stage == "Delivered" and not gh.argv("gh", "pr", "merge")
    body = g.next_pr_body("o/r", "Next change.", 21, w.state.thread, 2)
    assert "## From post-merge review by reviewer\n- rename foo\n- add a test for bar" in body
    poll(g, w)
    assert len(gh.api("repos/o/r/issues/9/comments")) == 1  # one reply per review
    g.open_pr("o/r", "next", "main", "Next", body)
    assert "github:carry:o/r" not in meta  # carried into the PR that was opened


def test_merged_pr_stops_polling_after_the_window():
    gh = FakeGh((PR, {"state": "MERGED", "mergedAt": MERGED_AT, "mergeCommit": {"oid": SQUASH}}))
    g, _ = make(gh, dataclasses.replace(GCFG, github=GitHubCfg(enabled=True, poll_interval=0, post_merge_window=60)))
    w = audit_world()
    end = parse_iso(MERGED_AT) + 61
    g.poll(w.state, end - 120)
    g.poll(w.state, end)
    g.poll(w.state, end + 10)
    assert len(gh.argv("gh", "pr", "view")) == 2  # inside the window, then the poll that finds it over; none after


# -- pr_id and the short-sha guard ---------------------------------------------------------
def test_two_prs_12_in_two_repos():
    a, b = "https://github.com/a/x/pull/12", "https://github.com/b/y/pull/12"
    assert contracts.pr_id(a) == ("a/x", "12") and contracts.pr_id("b/y#12") == ("b/y", "12") and contracts.pr_id("#12") == ("", "12")
    assert not contracts.same_pr(a, b) and not machine.head_matches(b, SHA, a, SHA) and machine.head_matches("#12", SHA, a, SHA)
    w = audit_world(pr=a)
    w.ev("sign_off", sender=REV, pr=b, sha=SHA, verdict="approve")
    assert w.state.stage == "Audit" and "sign-off from UREV ignored" in w.state.findings[-1]
    gh = FakeGh((a, {}), (b, {"latestReviews": [rv("R1", "reviewer", "APPROVED")]}))
    g, meta = make(gh, dataclasses.replace(GCFG, repos=(Repo("a/x", "driver"), Repo("b/y", "driver"))))
    out = poll(g, w)
    assert out.events == [] and w.state.stage == "Audit" and not gh.argv("gh", "pr", "merge")  # b/y#12's approval is not a/x#12's
    assert gh.argv("gh", "pr", "view")[0][3:6] == ["12", "-R", "a/x"] and f"github:pr:{w.state.thread}:a/x#12" in meta


def test_head_matches_short_sha_guard():
    assert machine.head_matches(PR, HEAD, PR, SHA) and machine.head_matches(PR, SHA, PR, HEAD)  # either side abbreviated
    assert not machine.head_matches(PR, HEAD[:6], PR, HEAD) and not machine.head_matches(PR, "abc12", PR, "abc12")  # under 7 digits names no head
    assert not machine.head_matches(PR, OLD, PR, SHA)


# -- R23: PR hygiene ------------------------------------------------------------------------
def test_refuses_to_open_a_pr_without_closes_study_board_milestone():
    g, _ = make(gh := FakeGh())
    good = g.next_pr_body("o/r", "Summary.", 21, "T1:C1:1.2", 2)
    assert contracts.pr_hygiene_missing(good) == [] and "Board: https://github.com/users/chengcli/projects/8" in good and "Milestone: R2" in good
    for line, name in (("Closes #21", "Closes #N"), ("Study thread: T1:C1:1.2", "study thread"), ("Board: https://github.com/users/chengcli/projects/8", "board link"), ("Milestone: R2", "milestone")):
        with pytest.raises(PrHygieneError, match=name): g.open_pr("o/r", "b", "main", "t", good.replace(line, ""))
    with pytest.raises(PrHygieneError, match="Closes #N"): g.open_pr("o/r", "b", "main", "t", g.next_pr_body("o/r", "s", None, "T", 2))
    no_board, _ = make(gh, dataclasses.replace(GCFG, board=BoardCfg()))
    with pytest.raises(PrHygieneError, match="board link"): no_board.open_pr("o/r", "b", "main", "t", no_board.next_pr_body("o/r", "s", 21, "T", 2))
    assert not gh.argv("gh", "pr", "create")
    url = g.open_pr("o/r", "b", "main", "t", good)
    assert url == "https://github.com/o/r/pull/13" and gh.argv("gh", "pr", "create")[0][-1] == good
    assert [d["variables"]["content"] for a, d in gh.calls if d and d.get("query") == board.M_ADD_ITEM] == ["PR_r_13"]
    assert ["gh", "api", "-X", "PATCH", "repos/o/r/issues/13", "-F", "milestone=1"] in gh.argv("gh", "api", "-X", "PATCH")
    assert gh.api("repos/o/r/milestones")[0][1] == {"title": "R2"}  # created on demand


def test_pr_under_audit_is_put_on_the_project_and_milestone_once():
    gh = FakeGh((PR, {}))
    g, _ = make(gh)
    w = audit_world()
    poll(g, w)
    poll(g, w)
    assert [d["variables"]["content"] for a, d in gh.calls if d and d.get("query") == board.M_ADD_ITEM] == ["PR_r_9"]
    assert gh.argv("gh", "api", "-X", "PATCH") == [["gh", "api", "-X", "PATCH", "repos/o/r/issues/9", "-F", "milestone=1"]]


# -- config ----------------------------------------------------------------------------------
def test_github_and_repos_config():
    c = config.parse('[github]\nenabled = true\npoll_interval = "5m"\npost_merge_window = "2d"\n[repos]\n"chengcli/fridica-research" = {merge = "driver", reviewers = ["alice"]}\n"chengcli/fridica" = {merge = "owner"}\n[people]\nU1 = "Alice"\n')
    assert c.github.enabled and c.github.poll_interval == 300 and c.github.post_merge_window == 172800 and "copilot" in c.github.bots
    assert c.repo("ChengCLI/fridica-research") == Repo("chengcli/fridica-research", "driver", ("alice",)) and c.repo("chengcli/fridica").merge == "owner" and c.repo("x/y").merge == "owner"
    assert c.slack_of("alice") == "U1" and c.slack_of("bob", {"U2": "bob"}) == "U2" and c.slack_of("nobody") == ""
    assert config.Config.from_dict(json.loads(json.dumps(c.to_dict()))) == c
    assert not config.parse("").github.enabled and config.parse("").repos == ()
    with pytest.raises(ValueError, match="merge"): config.parse('[repos]\n"o/r" = {merge = "anyone"}\n')
