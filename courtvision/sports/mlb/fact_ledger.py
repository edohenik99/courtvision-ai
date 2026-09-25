"""Create-once local MLB fact storage with atomic, no-overwrite publication.

The root is explicit (recommended local root: data/mlb/facts). Complete bytes
are flushed in a unique same-directory staging file, then hard-linked into
place, as in the existing MLB immutable artifact stores. Unsupported filesystems
fail closed; there is no overwrite-capable fallback. Atomicity is per record,
not a multi-record transaction. A killed writer may leave an uncommitted .tmp.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

from courtvision.sports.mlb.game_facts import (
    MLBFact, _id, canonical_json, fact_from_payload, fact_payload,
)


class FactLedgerConflict(ValueError):
    """Existing identity has different content or fails integrity validation."""


def _is_junction(path: Path) -> bool:
    # Path.is_junction is unavailable on supported Python 3.11. lstat exposes
    # the Windows mount-point tag without following the junction itself.
    mount_point_tag = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    try:
        result = path.lstat()
        return mount_point_tag is not None and getattr(result, "st_reparse_tag", None) == mount_point_tag
    except (FileNotFoundError, NotADirectoryError):
        return False


def _plain_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or _is_junction(part):
            raise ValueError("fact store does not accept symlinks or junctions")


def _resolved(path: Path) -> Path:
    # Windows can retain the extended-length prefix while parents are created
    # concurrently. It is the same path, not an escape from the configured root.
    value = str(path.resolve())
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = value[4:]
        if value.startswith("UNC\\"):
            value = "\\\\" + value[4:]
    return Path(value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _encoded(fact: MLBFact) -> bytes:
    payload = fact_payload(fact)
    fact_from_payload(payload)
    return canonical_json({"fact": payload, "factual_record_hash": fact.factual_record_hash}) + b"\n"


class MLBFactStore:
    """One immutable file for each game/player/role identity, regardless of season."""

    def __init__(self, root: str | Path):
        raw = Path(root).absolute()
        _plain_path(raw)
        self.root = _resolved(raw)

    def _path(self, role: str, game_id: str, player_id: str | None = None) -> Path:
        if role not in {"GAME", "BATTER", "PITCHER"}:
            raise ValueError("unsupported fact role")
        _id(game_id, "mlbam_game_id")
        if role == "GAME":
            if player_id is not None:
                raise ValueError("game identity cannot have a player ID")
            path = self.root / "game" / f"{game_id}.json"
        else:
            _id(player_id, "mlbam_player_id")
            path = self.root / role.lower() / game_id / f"{player_id}.json"
        _plain_path(path)
        if not _resolved(path).is_relative_to(self.root):
            raise ValueError("fact path escapes the configured root")
        return path

    def read(self, role: str, game_id: str, player_id: str | None = None) -> MLBFact:
        path = self._path(role, game_id, player_id)
        data = path.read_bytes()
        try:
            envelope = json.loads(data, object_pairs_hook=_unique_object)
            if not isinstance(envelope, dict) or set(envelope) != {"fact", "factual_record_hash"}:
                raise ValueError("invalid fact envelope")
            fact = fact_from_payload(envelope["fact"])
            expected = (game_id, role) if player_id is None else (game_id, player_id, role)
            if fact.logical_identity != expected or _encoded(fact) != data:
                raise ValueError("record identity, canonical bytes or hash mismatch")
            return fact
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise FactLedgerConflict("stored fact failed integrity validation") from exc

    def publish(self, fact: MLBFact) -> Path:
        data = _encoded(fact)
        player_id = getattr(fact, "mlbam_player_id", None)
        path = self._path(fact.role, fact.mlbam_game_id, player_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _plain_path(path)
        fd, name = tempfile.mkstemp(prefix=".fact-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # Publish only fully written/closed bytes. os.link cannot overwrite.
            _plain_path(path)
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing = self.read(fact.role, fact.mlbam_game_id, player_id)
                if _encoded(existing) != data:
                    raise FactLedgerConflict("conflicting fact for the same logical identity")
            return path
        finally:
            temporary.unlink(missing_ok=True)
