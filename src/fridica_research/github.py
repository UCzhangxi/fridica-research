"""GitHub reviews as the sign-off (R21), PR hygiene (R23), the driver-side merge and late reviews (R24): opt-in.

Enabled by `[github] enabled = true`; with it absent the driver behaves as before (Slack `SIGN-OFF`
lines only, no merge). Every gh call goes through one `Runner` (board.py's), so the
tests answer them with a fake. For the PR of a study in Audit, Deliver or Delivered, `poll`:

- requests every configured reviewer once per head through REST
  (`POST repos/<o>/<r>/pulls/<n>/requested_reviewers`), never the PR author and never a bot;
- reads `gh pr view --json latestReviews,headRefOid,...`; a review counts only when it is by a
  non-bot and on the current head: APPROVED -> `sign_off approve`, CHANGES_REQUESTED ->
  `sign_off changes` (login -> Slack id through `Config.slack_of`), COMMENTED -> a finding without
  verdict; each counted review is mirrored into the study thread as one SIGN-OFF line that the
  driver never parses back (`contracts.MIRROR_MARK`);
- for a `[repos]` entry with `merge = "driver"` squash-merges an open PR once at least one non-bot
  APPROVED review is on the current head and no CHANGES_REQUESTED is (`--match-head-commit`), and
  records the squash sha; for `merge = "owner"` it posts the approved PR once and waits;
- after the merge, a CHANGES_REQUESTED review gets one reply on the PR and one line in the thread
  ("acknowledged, goes into the next PR"), its items become findings, and the next PR body to that
  repository lists them under "From post-merge review by <login>".

State lives in the store's `meta` table (`github:pr:<thread>:<owner/repo#N>`, `github:carry:<repo>`),
never in the machine's snapshot: the machine learns about reviews only through events.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field

from . import contracts
from .board import M_ADD_ITEM, Projects, Runner, subprocess_runner
from .config import Config
from .machine import Event, State

log = logging.getLogger("fridica_research.github")
PR_FIELDS = "latestReviews,headRefOid,state,mergedAt,mergeCommit,author,milestone"
POLLED_STAGES = ("Audit", "Deliver", "Delivered")
Q_PR = "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){id}}}"
ACK = "acknowledged, goes into the next PR"


class PrHygieneError(ValueError):
    """A PR body without `Closes #N`, the study thread, the board link or the milestone (R23)."""


@dataclass
class Poll:
    events: list[Event] = field(default_factory=list)
    posts: list[tuple[str, str]] = field(default_factory=list)  # (ref suffix, text) for the study thread


def iso(ts: float) -> str: return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse_iso(s: str) -> float: return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def review_items(body: str) -> list[str]:
    """The items of a review body: its non-empty lines without list markers ("(no text)" for an empty body)."""
    items = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line).strip() for line in (body or "").splitlines()]
    return [i for i in items if i] or ["(no text)"]


class GitHub:
    def __init__(self, cfg: Config, runner: Runner | None = None, remember=lambda k, v: None, recall=lambda k: None):
        self.cfg, self.g = cfg, cfg.github
        self.run = runner or subprocess_runner(cfg.github.token_env)
        self.remember, self.recall = remember, recall
        self.projects = Projects(cfg, self.run, remember, recall)

    # -- gh primitives --------------------------------------------------------------
    def api(self, method: str, path: str, body: dict | None = None) -> str:
        return self.run(["gh", "api", "-X", method, path, *(["--input", "-"] if body is not None else [])], json.dumps(body) if body is not None else None)

    def view(self, pr: str) -> dict:
        repo, n = contracts.pr_id(pr)
        return json.loads(self.run(["gh", "pr", "view", n, "-R", repo, "--json", PR_FIELDS], None))

    def is_bot(self, author: dict | None) -> bool:
        a = author or {}
        login = str(a.get("login", "")).lower()
        return bool(a.get("is_bot")) or login.endswith("[bot]") or login in {b.lower() for b in self.g.bots}

    def request_reviews(self, pr: str, logins: list[str]):
        repo, n = contracts.pr_id(pr)
        self.api("POST", f"repos/{repo}/pulls/{n}/requested_reviewers", {"reviewers": logins})

    def merge(self, pr: str, head: str) -> dict:
        """`gh pr merge --squash` pinned to the head that was approved; returns the view after the merge (mergeCommit, mergedAt)."""
        repo, n = contracts.pr_id(pr)
        self.run(["gh", "pr", "merge", n, "-R", repo, "--squash", "--match-head-commit", head], None)
        return self.view(pr)

    def comment(self, pr: str, body: str):
        repo, n = contracts.pr_id(pr)
        self.api("POST", f"repos/{repo}/issues/{n}/comments", {"body": body})

    def milestone(self, repo: str, title: str) -> int:
        """The number of the generation milestone `title` (R1, R2, ...), created on demand."""
        for m in json.loads(self.run(["gh", "api", f"repos/{repo}/milestones?state=all&per_page=100"], None) or "[]"):
            if m.get("title") == title: return int(m["number"])
        return int(json.loads(self.api("POST", f"repos/{repo}/milestones", {"title": title}))["number"])

    def set_milestone(self, pr: str, title: str):
        repo, n = contracts.pr_id(pr)
        self.run(["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/{n}", "-F", f"milestone={self.milestone(repo, title)}"], None)

    def add_to_project(self, pr: str) -> str:
        repo, n = contracts.pr_id(pr)
        owner, name = repo.split("/", 1)
        node = self.projects.graphql(Q_PR, owner=owner, name=name, number=int(n))["repository"]["pullRequest"]["id"]
        return self.projects.graphql(M_ADD_ITEM, project=self.projects.project().id, content=node)["addProjectV2ItemById"]["item"]["id"]

    # -- R23: opening a PR ------------------------------------------------------------
    def board_url(self) -> str:
        b = self.cfg.board
        if not (b.enabled and b.owner and b.number): return ""
        return f"https://github.com/{'orgs' if b.owner_type == 'organization' else 'users'}/{b.owner}/projects/{b.number}"

    def carried(self, repo: str) -> dict:
        return json.loads(self.recall(f"github:carry:{repo.lower()}") or "{}")

    def next_pr_body(self, repo: str, summary: str, closes: int | None, thread: str, generation: int) -> str:
        """The R23 body for the next PR to `repo`, with the post-merge items carried from earlier reviews (R24)."""
        return contracts.pr_body(summary, closes, thread, self.board_url(), f"R{generation}", self.carried(repo))

    def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> str:
        """Refuses a body without the R23 lines; otherwise creates the PR, adds it to the project, sets the milestone."""
        missing = contracts.pr_hygiene_missing(body)
        if missing: raise PrHygieneError("refusing to open a PR without " + ", ".join(missing))
        url = self.run(["gh", "pr", "create", "-R", repo, "--head", head, "--base", base, "--title", title, "--body", body], None).strip().splitlines()[-1]
        self.add_to_project(url)
        self.set_milestone(url, re.search(r"^Milestone: (R\d+)", body, re.M).group(1))
        if all(f"## {contracts.POST_MERGE_HEAD}{login}" in body for login in self.carried(repo)): self.remember(f"github:carry:{repo.lower()}", None)
        return url

    # -- R21, R24: the poll ------------------------------------------------------------
    def poll(self, state: State, now: float) -> Poll:
        out = Poll()
        pr = state.implementer.get("pr", "")
        repo, n = contracts.pr_id(pr)
        if not (self.g.enabled and repo and state.stage in POLLED_STAGES): return out
        key = f"github:pr:{state.thread}:{repo}#{n}"
        t = json.loads(self.recall(key) or "{}")
        if t.get("done") or now < t.get("last_poll", 0) + self.g.poll_interval: return out
        t["last_poll"] = now
        try:
            v = self.view(pr)
            self.hygiene(pr, state, v, t)
            self.request(pr, v, t)
            self.reviews(pr, state, v, t, out, now)
            self.gate(pr, state, v, t, out, now)
        finally:
            self.remember(key, json.dumps(t, sort_keys=True))
        return out

    def hygiene(self, pr: str, state: State, v: dict, t: dict):
        """R23 for the PR under audit: on the study's project and on the generation milestone (once, retried until it succeeds)."""
        if t.get("hygiene"): return
        try:
            if self.board_url(): self.add_to_project(pr)
            if not v.get("milestone"): self.set_milestone(pr, f"R{state.generation}")
            t["hygiene"] = True
        except Exception as e:  # noqa: BLE001 - hygiene never stalls the sign-off
            log.warning("PR hygiene for %s failed: %s", pr, e)

    def reviewers(self, pr: str) -> list[str]:
        repo, _ = contracts.pr_id(pr)
        logins = self.cfg.repo(repo).reviewers or tuple(r.login for r in self.cfg.reviewers if r.login)
        return list(dict.fromkeys(logins))

    def request(self, pr: str, v: dict, t: dict):
        """R24: every configured reviewer is requested, once per head; never the author (an implementer never reviews their PR) or a bot."""
        head = v.get("headRefOid", "")
        if v.get("state") != "OPEN" or t.get("requested_head") == head: return
        author = str((v.get("author") or {}).get("login", "")).lower()
        logins = [lg for lg in self.reviewers(pr) if lg.lower() != author and not self.is_bot({"login": lg})]
        try:
            if logins: self.request_reviews(pr, logins)
            t["requested_head"], t["requested"] = head, logins
        except Exception as e:  # noqa: BLE001 - a reviewer without access stays on the Slack fallback
            log.warning("review request on %s failed: %s", pr, e)

    def reviews(self, pr: str, state: State, v: dict, t: dict, out: Poll, now: float):
        head = v.get("headRefOid", "")
        merged_at = v.get("mergedAt") or t.get("merged_at")
        seen = t.setdefault("seen", [])
        repo, n = contracts.pr_id(pr)
        for r in sorted(v.get("latestReviews") or [], key=lambda r: r.get("submittedAt") or ""):
            rid, kind = r.get("id") or f"{(r.get('author') or {}).get('login')}@{r.get('submittedAt')}", r.get("state")
            if rid in seen or kind not in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"): continue
            if self.is_bot(r.get("author")) or (r.get("commit") or {}).get("oid") != head:
                seen.append(rid)  # bots never count; a review of an older head never becomes current
                continue
            seen.append(rid)
            login = r["author"]["login"]
            after = bool(merged_at and (r.get("submittedAt") or "") > merged_at)
            out.posts.append((f"review-{rid}", contracts.mirror_line(login, kind, pr, head, after)))
            slack = self.cfg.slack_of(login, state.people)
            if kind == "COMMENTED":
                out.events.append(Event("finding", now, {"text": f"GitHub review by {login} on {repo}#{n} at {head[:12]} (COMMENTED, no verdict): {(r.get('body') or '(no text)').strip()[:500]}"}))
                continue
            if slack: out.events.append(Event("sign_off", now, {"sender": slack, "pr": pr, "sha": head, "verdict": "approve" if kind == "APPROVED" else "changes"}))
            if kind == "CHANGES_REQUESTED" and after: self.acknowledge(pr, login, r, out, now)

    def acknowledge(self, pr: str, login: str, r: dict, out: Poll, now: float):
        """R24: a changes-requested review after the merge: one reply on the PR, one thread line, findings, carried into the next PR."""
        repo, n = contracts.pr_id(pr)
        try: self.comment(pr, f"@{login} {ACK}.")
        except Exception as e:  # noqa: BLE001 - the thread line and the findings still record it
            log.warning("acknowledgement on %s failed: %s", pr, e)
        out.posts.append((f"ack-{r.get('id')}", f"{ACK}: post-merge review by {login} on {repo}#{n}"))
        items = review_items(r.get("body", ""))
        for item in items: out.events.append(Event("finding", now, {"text": f"post-merge review by {login} on {repo}#{n}: {item}"}))
        carry = self.carried(repo)
        carry[login] = [*carry.get(login, []), *items]
        self.remember(f"github:carry:{repo.lower()}", json.dumps(carry, sort_keys=True))

    def gate(self, pr: str, state: State, v: dict, t: dict, out: Poll, now: float):
        """R23/R24 merge: `merge = "driver"` repos only, at least one non-bot APPROVED and no CHANGES_REQUESTED on the current head."""
        repo, n = contracts.pr_id(pr)
        if v.get("state") == "MERGED":
            t.setdefault("merged_at", v.get("mergedAt"))
            t.setdefault("merged_sha", (v.get("mergeCommit") or {}).get("oid"))
            if t.get("merged_at") and now > parse_iso(t["merged_at"]) + self.g.post_merge_window: t["done"] = True
            return
        if v.get("state") != "OPEN":
            t["done"] = True
            return
        head = v.get("headRefOid", "")
        on_head = [r for r in v.get("latestReviews") or [] if (r.get("commit") or {}).get("oid") == head and not self.is_bot(r.get("author"))]
        approvers = sorted(r["author"]["login"] for r in on_head if r.get("state") == "APPROVED")
        if not approvers or any(r.get("state") == "CHANGES_REQUESTED" for r in on_head): return
        if self.cfg.repo(repo).merge != "driver":
            if t.get("owner_told") != head:
                t["owner_told"] = head
                out.posts.append((f"approved-{head[:12]}", f"{pr} approved on {head[:12]} by {', '.join(approvers)}; {repo} is merged by its owner"))
            return
        try: after = self.merge(pr, head)
        except Exception as e:  # noqa: BLE001 - retried on the next poll
            log.warning("merge of %s failed: %s", pr, e)
            return
        sha = (after.get("mergeCommit") or {}).get("oid", "")
        t.update(merged_sha=sha, merged_at=after.get("mergedAt") or iso(now))
        self.remember(f"github:revision:{repo.lower()}:g{state.generation}", sha)  # the squash sha is the generation's revision
        out.posts.append((f"merged-{head[:12]}", f"merged {repo}#{n} (squash) as {sha} after approval by {', '.join(approvers)} on {head[:12]}"))
