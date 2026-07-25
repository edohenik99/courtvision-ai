"""Explicit registry for approved sport-specific prediction engines."""

from __future__ import annotations

from collections.abc import Iterable

from courtvision.prediction.contracts import PredictionEngine


class PredictionEngineRegistryError(LookupError):
    """Raised when engine registration or selection is invalid."""


class PredictionEngineRegistry:
    """Small allowlist-backed engine registry keyed by sport and mode."""

    def __init__(self, engines: Iterable[PredictionEngine] = ()) -> None:
        self._engines: dict[tuple[str, str], PredictionEngine] = {}
        for engine in engines:
            self.register(engine)

    def register(self, engine: PredictionEngine) -> None:
        sport = str(engine.sport).strip().lower()
        modes = frozenset(str(mode).strip().lower() for mode in engine.modes)
        if not sport or not modes or "" in modes:
            raise PredictionEngineRegistryError(
                "prediction engines require a sport and at least one mode"
            )
        for mode in modes:
            key = (sport, mode)
            if key in self._engines:
                raise PredictionEngineRegistryError(
                    f"prediction engine already registered for {sport}/{mode}"
                )
            self._engines[key] = engine

    def resolve(self, sport: str, mode: str) -> PredictionEngine:
        key = (str(sport).strip().lower(), str(mode).strip().lower())
        try:
            return self._engines[key]
        except KeyError as exc:
            raise PredictionEngineRegistryError(
                f"no approved prediction engine for {key[0]}/{key[1]}"
            ) from exc

    def registered_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._engines))


__all__ = [
    "PredictionEngineRegistry",
    "PredictionEngineRegistryError",
]
