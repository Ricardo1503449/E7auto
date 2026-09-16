from __future__ import annotations
from e7auto.core.ports import Frame
from e7auto.core.observations import Observation
class NetworkVision:
    def __init__(self, config, matcher):
        self._config = config
        self.match = matcher.match

    def network_connection_error(self, frame: Frame) -> Observation | None:
        return self.match(frame, "network_connection_abnormal", self._config.rois["network_error"], self._config.default_confidence)


    def network_retry(self, frame: Frame) -> Observation | None:
        return self.match(frame, "network_retry", self._config.rois["network_retry"], self._config.default_confidence)
