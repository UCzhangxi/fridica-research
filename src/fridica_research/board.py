"""GitHub Projects v2 mirror (R3, R4, R8, R9, R10): opt-in, driver-written, never blocking.

One real issue in the study repository per study (parent) and one per stage run (sub-issue via
`addSubIssue`), each added to the project with `addProjectV2ItemById`. Fields are written with a
fixed set of GraphQL mutations through `gh api graphql`; number values are inlined in the
mutation text as GraphQL Floats (gh's `-F`/`--number` mangle them). Missing fields are created
once with `gh project field-create`. `Reviewers` is reserved by Projects v2, so the peer
reviewers live in `Peer reviewers`. Every failure is logged and retried on the next transition;
`sync` never raises into the driver. The Roadmap view's date fields (Start: Started, Target:
Projected finish) cannot be set through the API; docs/protocol.md gives the one-time setup.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
from typing import Callable

from .config import Config
from .machine import State

log = logging.getLogger("fridica_research.board")
Runner = Callable[[list[str]], str]
STAGE_OPTIONS = ("Explore", "Claim", "Debate", "Implement", "Audit", "Deliver", "Delivered", "Stopped")
FIELDS = {
    "Stage": "SINGLE_SELECT", "Iteration": "NUMBER", "Generation": "NUMBER", "Projected hours": "NUMBER", "Actual hours": "NUMBER",
    "Started": "DATE", "Projected finish": "DATE", "Finished": "DATE",
    "Owner": "TEXT", "Approach": "TEXT", "Workers": "TEXT", "Result": "TEXT", "Thread": "TEXT", "Follow-on": "TEXT", "Peer reviewers": "TEXT",
}
BOARD_STAGE = {"Blocked": "Stopped"}

Q_PROJECT = "query($owner:String!,$number:Int!){user(login:$owner){projectV2(number:$number){id fields(first:50){nodes{... on ProjectV2Field{id name dataType} ... on ProjectV2SingleSelectField{id name dataType options{id name}}}}}}}"
Q_USER = "query($login:String!){user(login:$login){id}}"
Q_ISSUE = "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){id}}}"
M_ADD_ITEM = "mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}"
M_SUB_ISSUE = "mutation($parent:ID!,$child:ID!){addSubIssue(input:{issueId:$parent,subIssueId:$child}){issue{id}}}"


def _mutation_value(kind: str, value) -> str:
    if kind == "NUMBER": return "{number: %s}" % repr(float(value))
    if kind == "DATE": return "{date: %s}" % json.dumps(str(value))
    if kind == "SINGLE_SELECT": return "{singleSelectOptionId: %s}" % json.dumps(str(value))
    return "{text: %s}" % json.dumps(str(value))


def field_mutation(project: str, item: str, field_id: str, kind: str, value) -> str:
    return "mutation{updateProjectV2ItemFieldValue(input:{projectId:%s,itemId:%s,fieldId:%s,value:%s}){projectV2Item{id}}}" % (json.dumps(project), json.dumps(item), json.dumps(field_id), _mutation_value(kind, value))


def subprocess_runner(token_env: str) -> Runner:
    def run(argv: list[str]) -> str:
        env = dict(os.environ)
        if token_env and os.environ.get(token_env): env["GH_TOKEN"] = os.environ[token_env]
        p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
        if p.returncode: raise RuntimeError(f"{argv[:3]} failed: {p.stderr.strip()[:300]}")
        return p.stdout
    return run


def day(ts: float | None) -> str | None: return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat() if ts else None


def stage_table(state: State) -> str:
    rows = ["| Stage | Start | Projected | End | Actual |", "|---|---|---|---|---|"]
    for r in state.stage_log:
        rows.append(f"| {r['stage']} (iteration {state.iteration if r is state.stage_log[-1] else ''}) | {_hm(r['start'])} | {_dur(r['projected'])} | {_hm(r['end'])} | {_dur(r['actual'])} |".replace(" (iteration )", ""))
    head = [f"Thread: {state.thread}", f"Generation {state.generation}, iteration {state.iteration}. Projected {state.projected_hours:g} h.", ""]
    return "\n".join(head + rows)


def _hm(ts): return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""
def _dur(s): return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}" if s is not None else ""


class Board:
    def __init__(self, cfg: Config, runner: Runner | None = None, remember: Callable[[str, str | None], None] = lambda k, v: None, recall: Callable[[str], str | None] = lambda k: None, issue_numbers: dict | None = None):
        self.cfg, self.b = cfg, cfg.board
        self.run = runner or subprocess_runner(cfg.board.token_env)
        self.remember, self.recall = remember, recall
        self.issue_numbers = issue_numbers or {}  # thread -> issue number given by `start --issue N`
        self.project: str | None = None
        self.fields: dict[str, dict] = {}

    # -- plumbing ---------------------------------------------------------------
    def graphql(self, query: str, **vars_) -> dict:
        argv = ["gh", "api", "graphql", "-f", f"query={query}"]
        for k, v in vars_.items(): argv += (["-F", f"{k}={v}"] if isinstance(v, int) else ["-f", f"{k}={v}"])
        out = json.loads(self.run(argv))
        if out.get("errors"): raise RuntimeError(str(out["errors"])[:300])
        return out["data"]

    def ensure_fields(self):
        if self.project: return
        data = self.graphql(Q_PROJECT, owner=self.b.owner, number=self.b.number)["user"]["projectV2"]
        self.project = data["id"]
        have = {f["name"]: f for f in data["fields"]["nodes"] if f}
        for name, kind in FIELDS.items():
            if name in have: continue
            argv = ["gh", "project", "field-create", str(self.b.number), "--owner", self.b.owner, "--name", name, "--data-type", kind]
            if kind == "SINGLE_SELECT": argv += ["--single-select-options", ",".join(STAGE_OPTIONS)]
            self.run(argv)
            data = self.graphql(Q_PROJECT, owner=self.b.owner, number=self.b.number)["user"]["projectV2"]
            have = {f["name"]: f for f in data["fields"]["nodes"] if f}
        self.fields = {n: {"id": f["id"], "kind": FIELDS[n], "options": {o["name"]: o["id"] for o in f.get("options", [])}} for n, f in have.items() if n in FIELDS}

    def set_field(self, item: str, name: str, value):
        if value is None or value == "": return
        f = self.fields[name]
        if f["kind"] == "SINGLE_SELECT": value = f["options"][value]
        self.run(["gh", "api", "graphql", "-f", "query=" + field_mutation(self.project, item, f["id"], f["kind"], value)])

    def set_fields(self, item: str, values: dict):
        for name, value in values.items(): self.set_field(item, name, value)

    def create_issue(self, title: str, body: str, assignee: str) -> tuple[int, str]:
        """`gh issue create` then the node id; returns (number, node id)."""
        argv = ["gh", "issue", "create", "-R", self.b.repo, "--title", title, "--body", body]
        if assignee: argv += ["--assignee", assignee]
        url = self.run(argv).strip().splitlines()[-1]
        number = int(url.rstrip("/").rsplit("/", 1)[1])
        return number, self.issue_id(number)

    def issue_id(self, number: int) -> str:
        owner, name = self.b.repo.split("/", 1)
        return self.graphql(Q_ISSUE, owner=owner, name=name, number=number)["repository"]["issue"]["id"]

    def add_item(self, content_id: str) -> str: return self.graphql(M_ADD_ITEM, project=self.project, content=content_id)["addProjectV2ItemById"]["item"]["id"]
    def edit_issue(self, number: int, *args: str): self.run(["gh", "issue", "edit", str(number), "-R", self.b.repo, *args])
    def close_issue(self, number: int): self.run(["gh", "issue", "close", str(number), "-R", self.b.repo])

    # -- the projection -------------------------------------------------------
    def cards(self, thread: str) -> dict:
        return json.loads(self.recall(f"board:{thread}") or "{}") or {"stages": []}

    def login(self, slack_id: str, state: State) -> str: return state.people.get(slack_id) or self.cfg.people.get(slack_id, "")

    def sync(self, state: State):
        """Mirror one study; every failure is logged and retried on the next transition."""
        if not self.b.enabled: return
        try:
            self.ensure_fields()
            cards = self.cards(state.thread)
            self.sync_study(state, cards)
            self.sync_stages(state, cards)
        except Exception as e:  # noqa: BLE001 - the board must never stall a stage
            log.warning("board update failed for %s: %s", state.thread, e)
        finally:
            if self.project and "cards" in locals(): self.remember(f"board:{state.thread}", json.dumps(cards))

    def title(self, state: State) -> str: return state.problem.strip().splitlines()[0][:80] if state.problem.strip() else state.thread

    def sync_study(self, state: State, cards: dict):
        owner_login = self.login(self.cfg.owner, state)
        body = stage_table(state)
        if "issue" not in cards:
            if state.thread in self.issue_numbers:
                number = int(self.issue_numbers[state.thread])
                node = self.issue_id(number)
                self.edit_issue(number, "--body", body, *(["--add-assignee", owner_login] if owner_login else []))
            else:
                number, node = self.create_issue(self.title(state), body, owner_login)
            cards["issue"], cards["issue_id"] = number, node
            cards["item"] = self.add_item(node)
            self.set_fields(cards["item"], {"Started": day(state.started_at), "Projected finish": day(state.started_at + state.projected_hours * 3600), "Thread": state.thread, "Owner": owner_login, "Generation": state.generation, "Projected hours": state.projected_hours})
        else:
            self.edit_issue(cards["issue"], "--body", body)
        reviewers = ", ".join(filter(None, (self.login(r.handle, state) for r in self.cfg.reviewers)))
        values = {"Stage": BOARD_STAGE.get(state.stage, state.stage), "Iteration": state.iteration, "Approach": (state.claim or {}).get("slug"), "Workers": ", ".join(f"{r}={w['worker_id']}" for r, w in sorted(state.workers.items())), "Result": (state.notes[-1] if state.stage in ("Stopped", "Blocked") and state.notes else state.deliverable.get("summary", "")[:500]), "Follow-on": state.followon, "Peer reviewers": reviewers}
        if state.finished_at:
            values.update({"Finished": day(state.finished_at), "Actual hours": round((state.finished_at - state.started_at) / 3600, 2)})
        self.set_fields(cards["item"], values)
        if state.stage in ("Delivered", "Stopped"): self.close_issue(cards["issue"])

    def sync_stages(self, state: State, cards: dict):
        owner_login = self.login(self.cfg.owner, state)
        stages = cards.setdefault("stages", [])
        for i, row in enumerate(state.stage_log):
            if i >= len(stages):
                title = f"{row['stage']} (iteration {state.iteration}): {self.title(state)}"[:120]
                number, node = self.create_issue(title, f"Stage run {i + 1} of {state.thread}; parent #{cards['issue']}.", owner_login)
                self.graphql(M_SUB_ISSUE, parent=cards["issue_id"], child=node)
                item = self.add_item(node)
                self.set_fields(item, {"Stage": row["stage"], "Started": day(row["start"]), "Projected finish": day(row["start"] + row["projected"]), "Projected hours": round(row["projected"] / 3600, 2), "Thread": state.thread, "Iteration": state.iteration, "Generation": state.generation})
                stages.append({"issue": number, "item": item, "closed": False})
            card = stages[i]
            if row["end"] is not None and not card["closed"]:
                self.set_fields(card["item"], {"Finished": day(row["end"]), "Actual hours": round(row["actual"] / 3600, 2), "Workers": ", ".join(f"{r}={w['worker_id']}" for r, w in sorted(state.workers.items()))})
                self.close_issue(card["issue"])
                card["closed"] = True
