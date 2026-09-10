"""Automation public entry points; implementations live in focused modules."""
from ..ports import GameVision
from .dependencies import AutomationDependencies, SystemClock
from .engine import AutomationEngine
from .session import AutomationSession
from .snapshots import SnapshotPublisher
from .stop_control import StopController, StopExecution

__all__ = ["AutomationDependencies", "AutomationEngine", "AutomationSession", "GameVision", "SnapshotPublisher", "StopController", "StopExecution", "SystemClock"]
