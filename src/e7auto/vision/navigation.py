"""Shared menu/tavern detection; no capture, input or feature dependencies."""


class StartupNavigationVision:
    def __init__(self, config, matcher):
        self.settings = config.startup_navigation
        self.matcher = matcher

    def _match(self, frame, key):
        scene = self.settings[key]
        return self.matcher.match_entry(frame, key, scene.region, scene.threshold,
                                        structure_threshold=scene.structure_threshold)

    def navigation_menu(self, frame):
        return (self._match(frame, "navigation_menu") or
                self._match(frame, "navigation_menu_notification"))

    def navigation_tavern(self, frame):
        return self._match(frame, "navigation_tavern")
