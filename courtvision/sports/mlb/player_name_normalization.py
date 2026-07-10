"""Canonical MLB player-name normalization for deterministic matching."""

from __future__ import annotations

import re
import unicodedata
from typing import Final


PLAYER_NAME_SUFFIXES: Final = frozenset({"jr", "sr", "ii", "iii", "iv"})
PLAYER_FIRST_NAME_NICKNAMES: Final = {
    "josh": "joshua",
    "cam": "cameron",
    "mike": "michael",
    "matt": "matthew",
    "alex": "alexander",
    "nick": "nicholas",
    "will": "william",
    "bill": "william",
    "bob": "robert",
    "rob": "robert",
    "tom": "thomas",
    "tony": "anthony",
    "chris": "christopher",
    "dan": "daniel",
    "ben": "benjamin",
    "zack": "zachary",
    "jim": "james",
}
_HYPHENS = "-\u2010\u2011\u2012\u2013\u2014\u2212"
_APOSTROPHES = "'\u2018\u2019\u201b\u2032\u00b4`"


def _ascii_casefold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(
        character for character in text if not unicodedata.combining(character)
    )


def _name_tokens(text: str) -> list[str]:
    text = re.sub(f"[{re.escape(_APOSTROPHES)}.]", "", text)
    text = re.sub(f"[{re.escape(_HYPHENS)}_/]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return [token for token in re.sub(r"\s+", " ", text).strip().split(" ") if token]


def _strip_suffixes(tokens: list[str]) -> list[str]:
    while tokens and tokens[-1] in PLAYER_NAME_SUFFIXES:
        tokens.pop()
    return tokens


def normalize_mlb_player_name(value: object) -> str:
    """Return the canonical MLB player-name comparison key.

    This intentionally performs deterministic normalization only. It does not
    score, infer, or guess beyond explicit first-name aliases.
    """

    text = _ascii_casefold(value)
    if "," in text:
        family, given = text.split(",", 1)
        tokens = _strip_suffixes(_name_tokens(given)) + _strip_suffixes(
            _name_tokens(family)
        )
    else:
        tokens = _strip_suffixes(_name_tokens(text))

    if not tokens:
        return ""

    tokens[0] = PLAYER_FIRST_NAME_NICKNAMES.get(tokens[0], tokens[0])
    return " ".join(tokens)


__all__ = [
    "PLAYER_FIRST_NAME_NICKNAMES",
    "PLAYER_NAME_SUFFIXES",
    "normalize_mlb_player_name",
]
