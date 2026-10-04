"""R7: replay this bootstrap study's stage sequence (explore -> claim -> debate, 2 rounds both revised ->
implement -> audit -> deliver) through the machine; assert the stage log and the board card sequence
(fridica-research issues #1 parent, #2-#7 stages, as on https://github.com/users/chengcli/projects/8)."""
from __future__ import annotations

import dataclasses
import json

from fridica_research import board
from fridica_research.config import Board as BoardCfg
from fridica_research.config import Config, Reviewer
from support import OWNER, World, report, result
from test_board import FakeGh

BOOTSTRAP = Config(owner=OWNER, channels=("C0C2D3PCW20",), max_iterations=3, max_debate_rounds=2, settle_window=60, stage_timeout=4 * 3600,
                   reviewers=(Reviewer("U_A", "numerics"), Reviewer("U_B", "scope"), Reviewer("U_C", "api")), people={OWNER: "chengcli", "U_A": "a", "U_B": "b", "U_C": "c"},
                   require_signoffs=False, board=BoardCfg(enabled=True, owner="chengcli", number=8, repo="chengcli/fridica-research"))
THREAD = "TJ6E2EJ2K:C0C2D3PCW20:1791125606.982449"
EXPLORE_REPORT = "Explore: control API, events, conventions.\n\n## Approaches\n- pure-machine-sqlite: pure step + SQLite snapshot -- the issue's design\n- notes-store: state in fridica thread notes -- one store\n- event-sourced-rebuild: replay the feed -- no local state\n"
STAGE_MIN = {"Explore": 6, "Claim": 1, "Debate": 8, "Implement": 90, "Audit": 90, "Deliver": 10}  # from the bootstrap stage log (EDT 10:53 ... )


def test_bootstrap_tape():
    w = World(cfg=BOOTSTRAP, now=1791125606.0)
    gh = FakeGh()
    meta = {}
    b = board.Board(BOOTSTRAP, runner=gh, remember=meta.__setitem__, recall=meta.get)
    def sync(): b.sync(w.state)
    w.start(thread=THREAD, problem="Bootstrap: implement fridica-research (fridica #126)")
    sync()
    w.now += STAGE_MIN["Explore"] * 60
    w.finish("explorer", result(report=EXPLORE_REPORT, summary="three package shapes"))
    sync()
    assert w.state.claim["slug"] == "pure-machine-sqlite"
    w.now += STAGE_MIN["Claim"] * 60
    w.tick(60)  # settle: no peer claimed
    sync()
    assert w.state.stage == "Debate" and w.state.round == 1
    # Round 1: both revised; round 2: both revised (max reached) -> synthesis.
    w.finish("mathematician", result(report=report(position="revised", body="state space, transition table, invariants")))
    w.finish("physicist", result(report=report(position="revised", body="scales, balances, five tests")))
    assert w.state.round == 2 and [a["role"] for a in w.kinds("delegate")[-2:]] == ["mathematician", "physicist"]
    w.now += STAGE_MIN["Debate"] * 60
    w.finish("mathematician", result(report=report(position="revised", body="concede snapshot store; keep waiting ledger")))
    w.finish("physicist", result(report=report(position="revised", body="concede snapshot; hold no log")))
    sync()
    assert w.state.stage == "Implement" and w.state.synthesis["synthesis"]
    w.now += STAGE_MIN["Implement"] * 60
    w.finish("implementer", result(status="done", artifacts=["https://github.com/chengcli/fridica-research/pull/2"], machine_state={"branch": "study/126-bootstrap", "commit": "deadbeef", "dirty": False}))
    sync()
    assert w.state.stage == "Audit"
    req = [p for p in w.kinds("post") if p["post_kind"] == "report"][-1]["text"]
    assert "<@U_A> (numerics)" in req and "<@U_B> (scope)" in req and "<@U_C> (api)" in req and "sha: deadbeef" in req
    w.ev("sign_off", sender="U_A", pr="https://github.com/chengcli/fridica-research/pull/2", sha="deadbeef", verdict="approve")
    w.now += STAGE_MIN["Audit"] * 60
    w.finish("auditor", result(report=report(verdict="pass")))  # deliver LLM call and posts follow at once
    assert w.state.stage == "Delivered"
    sync()
    # Stage log: one row per stage, in order, each closed, projected from config, actual from the clock.
    assert [r["stage"] for r in w.state.stage_log] == ["Explore", "Claim", "Debate", "Implement", "Audit", "Deliver"]
    assert all(r["end"] is not None and r["actual"] >= 0 for r in w.state.stage_log)
    assert [r["projected"] for r in w.state.stage_log] == [600, 120, 1200, 5400, 5400, 600]
    assert round(w.state.stage_log[2]["actual"] / 60) == STAGE_MIN["Debate"] and round(w.state.stage_log[3]["actual"] / 60) == STAGE_MIN["Implement"]
    # Delivery post: projected vs actual (R4), PR, sha, audit verdict.
    res = [p for p in w.kinds("post") if p["post_kind"] == "study_result"][-1]["text"]
    assert "projected: 4 h, actual: 3.27 h" in res and "pr: https://github.com/chengcli/fridica-research/pull/2" in res and "audit: pass" in res
    # Three LLM calls, six delegates (explorer, 2x2 debate, implementer, auditor).
    assert [a["name"] for a in w.kinds("llm_call")] == ["study_brief", "study_synthesis", "study_deliver"]
    assert [a["role"] for a in w.kinds("delegate")] == ["explorer", "mathematician", "physicist", "mathematician", "physicist", "implementer", "auditor"]
    # Board (R9): issue #1 is the study, #2-#7 its stage runs in order, all closed, parent closed last.
    cards = json.loads(meta[f"board:{THREAD}"])
    assert cards["issue"] == 1 and [c["issue"] for c in cards["stages"]] == [2, 3, 4, 5, 6, 7] and cards["closed"]
    titles = [a[6] for a in gh.argv("gh", "issue", "create")]
    assert titles[0] == "Bootstrap: implement fridica-research (fridica #126)"
    assert [t.split(":")[0] for t in titles[1:]] == [f"{s} (iteration 1)" for s in ("Explore", "Claim", "Debate", "Implement", "Audit", "Deliver")]
    assert gh.sets(board.M_SUB_ISSUE) == [{"parent": "I_1", "child": f"I_{n}"} for n in range(2, 8)]
    assert [a[3] for a in gh.argv("gh", "issue", "close")] == ["2", "3", "4", "5", "6", "7", "1"]
    assert any(v["item"] == "PVTI_I_1" and v["field"] == "F_Peer_reviewers" and v["v"] == "a, b, c" for v in gh.sets(board.M_SET_TEXT))
    stage_opts = [v["v"] for v in gh.sets(board.M_SET_OPTION) if v["item"] == "PVTI_I_1"]
    assert stage_opts == ["O_Explore", "O_Claim", "O_Debate", "O_Implement", "O_Audit", "O_Delivered"]


def test_bootstrap_tape_with_signoffs_required_waits_then_delivers():
    cfg = dataclasses.replace(BOOTSTRAP, require_signoffs=True, board=BoardCfg())
    w = World(cfg=cfg)
    w.to_audit()
    w.finish("auditor", result(report=report(verdict="pass")))
    assert w.state.stage == "Audit" and w.state.phase == "signoff"
    w.ev("sign_off", sender="U_A", pr="p", sha="s", verdict="approve")
    w.ev("sign_off", sender="U_B", pr="p", sha="s", verdict="approve")
    assert w.state.stage == "Audit"
    w.tick(cfg.stage_timeout)  # U_C never answers: deliver with the missing sign-off listed
    assert w.state.stage == "Delivered" and w.state.audit["signoffs_missing"] == ["U_C"]
    assert "sign-offs missing: U_C" in [p for p in w.kinds("post") if p["post_kind"] == "study_result"][-1]["text"]
