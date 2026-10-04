"""research.toml: the driver's configuration (stdlib tomllib)."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PATH = "~/.config/fridica-research/research.toml"
STAGES = ("explore", "claim", "debate", "implement", "audit", "deliver")
DEFAULT_PROJECTION = {"explore": 600, "claim": 120, "debate": 1200, "implement": 5400, "audit": 5400, "deliver": 600}
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$")
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def duration(value, default: float) -> float:
    """`"10m"`, `"2h"`, `90` (seconds) -> seconds."""
    if value is None: return default
    if isinstance(value, (int, float)): return float(value)
    m = _DURATION.match(str(value))
    if not m: raise ValueError(f"bad duration {value!r}")
    return float(m.group(1)) * _UNIT[m.group(2)]


@dataclass(frozen=True)
class Reviewer:
    """A peer auditor (R13): the Slack id, the GitHub login that owns the audit card, and the scope they take."""
    handle: str  # Slack user id (U…)
    focus: str = ""  # the audit scope this reviewer takes
    login: str = ""  # GitHub login; empty -> the driver asks in the thread (R8) and leaves the card unassigned


@dataclass(frozen=True)
class Board:
    enabled: bool = False
    owner: str = ""
    number: int = 0
    repo: str = ""  # owner/name of the study repository where cards are real issues
    token_env: str = "GH_TOKEN"
    owner_type: str = "user"  # user | organization


@dataclass(frozen=True)
class Config:
    socket: str = "~/.local/state/fridica/control.sock"
    capability_file: str | None = None
    owner: str = ""  # the owner's Slack user id; own posts are `sender == owner`
    state_path: str = "~/.local/state/fridica-research/state.sqlite3"
    channels: tuple[str, ...] = ()
    starters: tuple[str, ...] = ()
    max_iterations: int = 3
    max_debate_rounds: int = 2
    auditor_backend: str = "other"
    auto_followon: bool = True
    max_generations: int = 5
    stage_timeout: float = 7200.0
    settle_window: float = 60.0
    idle_sleep: float = 2.0
    default_projected_hours: float = 4.0
    projection: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PROJECTION))
    board: Board = field(default_factory=Board)
    reviewers: tuple[Reviewer, ...] = ()
    audit_scopes: tuple[str, ...] = ()  # all scopes; those no reviewer takes go to a local auditor worker
    require_signoffs: bool = True
    people: dict[str, str] = field(default_factory=dict)  # Slack user id -> GitHub login
    llm_model: str = "haiku"

    @property
    def socket_path(self) -> Path: return Path(os.path.expanduser(self.socket))

    def login_of(self, slack_id: str, learned: dict | None = None) -> str:
        """GitHub login for a Slack id: the reviewer entry, `[people]`, or a login learned in the thread."""
        for r in self.reviewers:
            if r.handle == slack_id and r.login: return r.login
        return self.people.get(slack_id) or (learned or {}).get(slack_id, "")

    def uncovered_scopes(self) -> tuple[str, ...]:
        """Audit scopes no peer reviewer takes: the local auditor's work (R13). No scopes at all -> one local audit."""
        taken = {r.focus for r in self.reviewers if r.focus}
        return tuple(s for s in self.audit_scopes if s not in taken) or (() if self.reviewers else ("scope",))
    @property
    def state_file(self) -> Path: return Path(os.path.expanduser(self.state_path))

    def to_dict(self) -> dict: return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """Inverse of `to_dict` (the replay corpus stores the config as JSON)."""
        d = dict(d)
        d["board"] = Board(**d.get("board", {}))
        d["reviewers"] = tuple(Reviewer(**r) for r in d.get("reviewers", ()))
        for k in ("channels", "starters", "audit_scopes"): d[k] = tuple(d.get(k, ()))
        return cls(**d)


def parse(text: str) -> Config:
    raw = tomllib.loads(text)
    f = raw.get("fridica", {})
    proj = dict(DEFAULT_PROJECTION)
    for k, v in raw.get("projection", {}).items():
        if k not in STAGES: raise ValueError(f"[projection] unknown stage {k}")
        proj[k] = duration(v, proj[k])
    b = raw.get("board", {})
    a = raw.get("audit", {})
    reviewers = tuple(_reviewer(r) for r in a.get("reviewers", []))
    scopes = tuple(a.get("scopes", [])) or tuple(dict.fromkeys(r.focus for r in reviewers if r.focus))
    return Config(
        socket=f.get("socket", Config.socket), capability_file=f.get("capability_file"), owner=str(f.get("owner", "")),
        state_path=raw.get("state_path", Config.state_path),
        channels=tuple(raw.get("channels", [])), starters=tuple(raw.get("starters", [])),
        max_iterations=int(raw.get("max_iterations", 3)), max_debate_rounds=int(raw.get("max_debate_rounds", 2)),
        auditor_backend=str(raw.get("auditor_backend", "other")), auto_followon=bool(raw.get("auto_followon", True)),
        max_generations=int(raw.get("max_generations", 5)), stage_timeout=duration(raw.get("stage_timeout"), 7200.0),
        settle_window=duration(raw.get("settle_window"), 60.0), idle_sleep=duration(raw.get("idle_sleep"), 2.0),
        default_projected_hours=float(raw.get("default_projected_hours", 4.0)), projection=proj,
        board=Board(bool(b.get("enabled", False)), str(b.get("owner", "")), int(b.get("number", 0)), str(b.get("repo", "")), str(b.get("token_env", "GH_TOKEN")), str(b.get("owner_type", "user"))),
        reviewers=reviewers, audit_scopes=scopes, require_signoffs=bool(a.get("require_signoffs", True)),
        people={str(k): str(v) for k, v in raw.get("people", {}).items()}, llm_model=str(raw.get("llm_model", "haiku")),
    )


def _reviewer(r) -> Reviewer:
    if not isinstance(r, dict): return Reviewer(str(r))
    return Reviewer(str(r.get("handle") or r.get("slack") or ""), str(r.get("scope") or r.get("focus") or ""), str(r.get("login", "")))


def load(path: str | Path | None = None) -> Config:
    p = Path(os.path.expanduser(str(path or DEFAULT_PATH)))
    return parse(p.read_text()) if p.exists() else Config()
