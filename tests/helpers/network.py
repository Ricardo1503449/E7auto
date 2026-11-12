"""Explicit test-only network dependency for the existing game-side models."""
from e7auto.core.observations import NetworkObservation, Observation
from e7auto.runtime.network import NetworkService


class GameNetworkDetector:
    def __init__(self, game):
        self.game = game

    @staticmethod
    def _observation(value):
        if value is None or isinstance(value, Observation):
            return value
        return Observation(value.object_id, value.confidence, value.bounds, value.anchor)

    def observe(self, frame):
        loading = getattr(self.game, "network_loading", lambda _: None)(frame)
        error = getattr(self.game, "network_connection_error", lambda _: None)(frame)
        retry = (getattr(self.game, "network_retry", lambda _: None)(frame)
                 if error is not None else None)
        return NetworkObservation(self._observation(loading), self._observation(error),
                                  self._observation(retry))


def game_network_service(game):
    return NetworkService(GameNetworkDetector(game), clear_frames=3, clear_ms=300)
