"""Pure, corroborated MLB factual finality; never settlement or score inference."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


FINAL_DETAILED_STATES = frozenset({"final", "game over", "completed early"})
NONFINAL_DETAILED_STATES = frozenset({
    "delayed", "in progress", "manager challenge", "postponed", "scheduled",
    "suspended", "warmup", "pre-game", "preview", "live", "cancelled", "canceled",
})
_NONFINAL_CODES = frozenset({"s", "p", "i", "d"})
_NONFINAL_STATUS_CODES = _NONFINAL_CODES | {"di", "dr", "dd"}


@dataclass(frozen=True, slots=True)
class MLBGameFinality:
    canonical_state: Literal["FINAL", "NON_FINAL", "AMBIGUOUS", "CONFLICT"]
    abstract_state: object
    detailed_state: object
    coded_state: object
    status_code: object
    abstract_code: object
    decision_reason: str

    @property
    def is_final(self) -> bool:
        return self.canonical_state == "FINAL"


def classify_game_finality(status: Mapping[str, object]) -> MLBGameFinality:
    """Classify raw StatsAPI fields, retaining supplied values without rewriting.

    Abstract Final needs a recognized detailed terminal state or explicit F code.
    Every supplied field must be compatible. FR is only a retained companion of
    the preserved Final / Completed Early / F tuple, never a finality witness.
    Missing fields differ from explicitly malformed/blank fields.
    """
    if not isinstance(status, Mapping):
        raise TypeError("provider status must be a mapping")
    fields = ("abstractGameState", "detailedState", "codedGameState", "statusCode", "abstractGameCode")
    raw = tuple(status.get(key) for key in fields)

    def result(state, reason):
        return MLBGameFinality(state, *raw, reason)

    if any(key in status and (not isinstance(status[key], str) or not status[key].strip())
           for key in fields):
        return result("AMBIGUOUS", "malformed_or_blank_status_field")
    abstract, detailed, coded, code, abstract_code = (
        value.strip().casefold() if isinstance(value, str) else "" for value in raw)
    positive = (abstract == "final" or detailed in FINAL_DETAILED_STATES
                or coded == "f" or code == "f" or abstract_code == "f")
    negative = (abstract in NONFINAL_DETAILED_STATES or detailed in NONFINAL_DETAILED_STATES
                or coded in _NONFINAL_CODES or code in _NONFINAL_STATUS_CODES
                or abstract_code in _NONFINAL_CODES)
    if positive and negative:
        return result("CONFLICT", "terminal_and_nonfinal_status_fields_disagree")
    if negative:
        return result("NON_FINAL", "explicit_nonfinal_status")
    if abstract and abstract != "final":
        return result("AMBIGUOUS", "unrecognized_abstract_state")
    if detailed and detailed not in FINAL_DETAILED_STATES:
        return result("AMBIGUOUS", "unrecognized_detailed_state")
    if (coded and coded != "f") or (abstract_code and abstract_code != "f"):
        return result("AMBIGUOUS", "unrecognized_status_code")
    observed_fr_tuple = abstract == "final" and detailed == "completed early" and coded == "f"
    if code and code != "f" and not (code == "fr" and observed_fr_tuple):
        return result("AMBIGUOUS", "unrecognized_or_uncorroborated_status_code")
    if abstract != "final":
        return result("AMBIGUOUS", "abstract_final_required")
    witnesses = []
    if detailed in FINAL_DETAILED_STATES:
        witnesses.append("recognized_detailed_state")
    if coded == "f":
        witnesses.append("codedGameState=F")
    if code == "f":
        witnesses.append("statusCode=F")
    if not witnesses:
        return result("AMBIGUOUS", "terminal_corroboration_required")
    return result("FINAL", "abstractGameState=Final corroborated by " + ", ".join(witnesses))
