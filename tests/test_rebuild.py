"""The snapshot equals the fold of `step` over the events (synthesis decision 1): replaying a study's
event tape from a fresh State reproduces the stored snapshot and the same actions, byte for byte."""
from __future__ import annotations

import json

from fridica_research import machine
from fridica_research.store import Store
from support import CFG, World


def fold(w: World):
    s, actions = machine.start(w.state.thread, "C1", w.state.problem, w.events[0].now if w.events else w.now, projected_hours=4.0, cfg=w.cfg)
    out = list(actions)
    for ev in w.events:
        s, a = machine.step(s, ev, w.cfg)
        out.extend(a)
    return s, out


def test_fold_reproduces_snapshot_and_actions():
    w = World()
    w.to_delivered()
    s, actions = fold(w)
    assert s.to_dict() == w.state.to_dict()
    assert [a.to_dict() for a in actions] == [a.to_dict() for a in w.actions]


def test_fold_with_failures_and_repicks():
    w = World(auto_post=False)
    w.to_claim()
    w.ev("peer_post", ts="1700000000.000001", sender="UPEER", kind="study_claim", text="Claim (iteration 1): x\napproach: alpha\nwhy: w\nalso considered: none")
    w.ev("own_post_seen", ts="1700000000.000002", kind="study_claim", text=w.kinds("post")[0]["text"])
    w.tick(CFG.stage_timeout)
    s, actions = fold(w)
    assert s.to_dict() == w.state.to_dict() and len(actions) == len(w.actions)


def test_store_roundtrip_is_lossless(tmp_path):
    w = World()
    w.to_implement()
    store = Store(tmp_path / "s.sqlite3")
    store.save(w.state, cursor=42)
    loaded = store.load(w.state.thread)
    assert loaded.to_dict() == w.state.to_dict() and store.cursor == 42
    assert json.loads(store.db.execute("SELECT state_json FROM studies").fetchone()[0])["stage"] == "Implement"
    assert store.command(w.state.thread) is None and store.command(w.state.thread, "stop") == "stop" and store.command(w.state.thread) == "stop" and store.command(w.state.thread) is None
    store.close()
