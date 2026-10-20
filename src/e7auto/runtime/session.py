from __future__ import annotations

from typing import Callable
import traceback

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.runtime.dependencies import AutomationDependencies
from e7auto.runtime.contracts import FeatureFlow, RunPolicy
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopExecution, StopController


class RuntimeSession:
    def __init__(
        self,
        dependencies: AutomationDependencies,
        control: StopController,
        on_snapshot: Callable[[RuntimeSnapshot], None],
    ) -> None:
        self._dependencies = dependencies
        self._on_snapshot = on_snapshot
        self._control = control

    def request_stop(self) -> None:
        self._control.request(StopReason.MANUAL_STOP)

    def _event(self, event: str, **fields: object) -> None:
        try:
            self._dependencies.logger.event(event, **fields)
        except Exception:
            pass  # Diagnostics cannot replace the stop reason or interrupt cleanup.

    def run(self, initial: RuntimeSnapshot,
            flow_factory: Callable[[StopController, SnapshotPublisher], FeatureFlow],
            policy: RunPolicy) -> RuntimeSnapshot:
        publisher = SnapshotPublisher(initial, self._on_snapshot)
        engine: FeatureFlow | None = None
        reason = StopReason.INTERNAL_ERROR
        detail = ""
        try:
            self._on_snapshot(initial)
            self._control.checkpoint()
            engine = flow_factory(self._control, publisher)
            engine.execute()
            reason = StopReason.INTERNAL_ERROR
            detail = "engine returned without terminal reason"
        except StopExecution as exc:
            reason = exc.reason
            detail = exc.detail
        except Exception as exc:
            reason = StopReason.INTERNAL_ERROR
            detail = repr(exc)
            self._event("internal_error", error=detail, traceback=traceback.format_exc())
        finally:
            if engine is not None and reason in policy.normal_completion:
                try:
                    engine.finish_normal_run(reason)
                except StopExecution as exc:
                    reason = exc.reason
                    detail = exc.detail
                except Exception as exc:
                    reason = StopReason.INTERNAL_ERROR
                    detail = f"normal completion cleanup failed: {exc!r}"
                    self._event("internal_error", error=detail, traceback=traceback.format_exc())
            try:
                if reason not in policy.expected_stops:
                    self._dependencies.logger.save_stop_snapshot(
                        engine.cached_game_frame() if engine is not None else None,
                        stop_reason=reason.value,
                        stopped_monotonic=self._dependencies.clock.monotonic(),
                    )
            except Exception as exc:
                # A diagnostic writer must never replace the automation failure or skip cleanup.
                self._event(
                    "stop_snapshot", outcome="failed", stop_reason=reason.value,
                    source="last_successful_capture", detail=f"snapshot writer failed: {exc!r}",
                )
            if engine is not None:
                try:
                    engine.release_cached_game_frame()
                except Exception as exc:
                    detail = f"{detail}; feature cleanup failed: {exc!r}".strip("; ")
            try:
                clear = getattr(self._dependencies.vision, "clear_template_cache", None)
                if clear is not None:
                    clear()
            except Exception as exc:
                detail = f"{detail}; template cache cleanup failed: {exc}".strip("; ")
            try:
                self._dependencies.capture.close()
            except Exception as exc:
                detail = f"{detail}; capture close failed: {exc}".strip("; ")
            if reason is StopReason.MANUAL_STOP:
                self._event("stop_requested", source="stop_button")
            try:
                try:
                    publisher.finalize(reason)
                finally:
                    final = publisher.snapshot
                    self._event(
                        "run_stopped",
                        reason=reason.value,
                        detail=detail,
                        refresh_spent=final.refresh_spent,
                        refresh_limit=final.refresh_limit,
                        feature_id=final.feature_id,
                        purchases_completed=final.purchases_completed,
                        purchase_limit=final.purchase_limit,
                        rounds_completed=final.rounds_completed,
                        round_limit=final.round_limit,
                        defeats=final.defeats,
                    )
            finally:
                self._dependencies.logger.close()
        return publisher.snapshot
