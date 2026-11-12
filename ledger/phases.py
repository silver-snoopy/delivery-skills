"""Maps a ticket's board state to a Phase using a team's configured rules."""
from typing import Optional, Tuple

from .config import Config, PhaseRule, Team
from .errors import LedgerError
from .model import Phase


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


class PhaseMapper:
    def __init__(self, team: Team, cfg: Config):
        self.team = team
        self._not_delivered_states = {_norm(s)
                                      for s in cfg.not_delivered_states}
        self._not_delivered_reasons = {_norm(s)
                                       for s in cfg.not_delivered_reasons}

    @staticmethod
    def _matches(rule: PhaseRule, state: str, column: Optional[str],
                 column_done: Optional[bool]) -> bool:
        if _norm(rule.state) != _norm(state):
            return False
        if rule.column is not None and _norm(rule.column) != _norm(column):
            return False
        if rule.column_done is not None and rule.column_done != column_done:
            return False
        return True

    def map(self, state: str, column: Optional[str],
            column_done: Optional[bool]) -> Tuple[Phase, Optional[str]]:
        for rule in self.team.phases:
            if self._matches(rule, state, column, column_done):
                return Phase[rule.phase.upper()], rule.wait_label
        where = "state %r" % state
        if column:
            where += " in column %r" % column
        raise LedgerError(
            "Team %s has no phase rule for %s." % (self.team.name, where),
            "Add a rule for it under that team's phases in config.json.")

    def not_delivered(self, state: str, reason: str) -> bool:
        return (_norm(state) in self._not_delivered_states
                or _norm(reason) in self._not_delivered_reasons)


class AreaPhaseMapper:
    """Picks, for each revision, the rules of the team that owns the
    revision's area (the default team when no configured team does)."""

    def __init__(self, cfg: Config, default_team: str):
        self._cfg, self._default = cfg, default_team
        self._mappers = {}

    def for_area(self, area: str) -> PhaseMapper:
        name = self._cfg.team_for_area(area) or self._default
        if name not in self._mappers:
            self._mappers[name] = PhaseMapper(self._cfg.team(name),
                                              self._cfg)
        return self._mappers[name]
