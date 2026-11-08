"""Visits sweeps one by one and looks around until their tiles are on disk."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from .coverage import SweepCoverage, TileLevel
from .moves import LookPattern, ViewMove
from .sweeps import Sweep
from .traffic import SweepLoad, Traffic


class Viewer(Protocol):
    def open_sweep(self, sweep: Sweep) -> SweepLoad: ...
    def look(self, move: ViewMove) -> Traffic: ...
    def fetch_tiles(self, sweep: Sweep, file_names: tuple[str, ...]) -> Traffic: ...


class CoverageSource(Protocol):
    def for_sweep(self, sweep_uuid: str) -> SweepCoverage: ...


@dataclass(frozen=True)
class WalkSettings:
    target_level: TileLevel = "2k"
    max_passes: int = 2                # look-around passes per sweep while still incomplete
    include_complete: bool = False     # also visit sweeps that already have every target tile
    fetch_unviewable: bool = True      # after looking around, request still-missing tiles directly
    stop_on_rate_limit: bool = True    # end the walk at the first HTTP 429 instead of pressing on
    pattern: LookPattern = LookPattern()


class VisitOutcome(Enum):
    SKIPPED_COMPLETE = "already complete"
    LOAD_FAILED = "viewer did not load"
    RATE_LIMITED = "rate limited by Matterport"
    WALKED = "walked"


@dataclass(frozen=True)
class SweepVisit:
    sweep: Sweep
    level: TileLevel
    outcome: VisitOutcome
    before: SweepCoverage
    after: SweepCoverage
    traffic: Traffic
    passes: int = 0
    fetched_directly: int = 0

    def gained(self) -> int:
        return self.after.present_count(self.level) - self.before.present_count(self.level)


@dataclass
class WalkResult:
    target_level: TileLevel
    visits: list[SweepVisit] = field(default_factory=list)
    stopped_for_rate_limit: bool = False

    @property
    def traffic(self) -> Traffic:
        total = Traffic()
        for visit in self.visits:
            total.add(visit.traffic)
        return total


class WalkObserver(Protocol):
    """Progress callbacks. `number` is 1-based; `total` is the number of sweeps or moves."""
    def sweep_started(self, sweep: Sweep, number: int, total: int) -> None: ...
    def sweep_loaded(self, sweep: Sweep, load: SweepLoad) -> None: ...
    def move_done(self, move: ViewMove, number: int, total: int, traffic: Traffic) -> None: ...
    def pass_done(self, pass_number: int, traffic: Traffic, coverage: SweepCoverage, level: TileLevel) -> None: ...
    def direct_fetch_done(self, requested: int, traffic: Traffic) -> None: ...
    def sweep_done(self, visit: SweepVisit, number: int, total: int) -> None: ...


class _Silent:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class TourWalker:
    """Opens each sweep in the viewer, looks around it, and records tile coverage before/after."""

    def __init__(self, viewer: Viewer, coverage: CoverageSource, settings: WalkSettings = WalkSettings(),
                 observer: WalkObserver | None = None):
        self._viewer = viewer
        self._coverage = coverage
        self._settings = settings
        self._observer: WalkObserver = observer or _Silent()

    def walk(self, sweeps: tuple[Sweep, ...]) -> WalkResult:
        result = WalkResult(self._settings.target_level)
        for number, sweep in enumerate(sweeps, 1):
            self._observer.sweep_started(sweep, number, len(sweeps))
            visit = self._visit(sweep)
            result.visits.append(visit)
            self._observer.sweep_done(visit, number, len(sweeps))
            if visit.outcome is VisitOutcome.RATE_LIMITED and self._settings.stop_on_rate_limit:
                result.stopped_for_rate_limit = True
                break
        return result

    def _visit(self, sweep: Sweep) -> SweepVisit:
        s, level = self._settings, self._settings.target_level
        before = after = self._coverage.for_sweep(sweep.uuid)
        traffic = Traffic()

        def visit(outcome: VisitOutcome, passes: int = 0, fetched: int = 0) -> SweepVisit:
            return SweepVisit(sweep, level, outcome, before, self._coverage.for_sweep(sweep.uuid),
                              traffic, passes, fetched)

        def rate_limited(step: Traffic) -> bool:
            traffic.add(step)
            return step.rate_limited and s.stop_on_rate_limit

        if before.is_complete(level) and not s.include_complete:
            return visit(VisitOutcome.SKIPPED_COMPLETE)
        load = self._viewer.open_sweep(sweep)
        self._observer.sweep_loaded(sweep, load)
        if rate_limited(load.traffic):
            return visit(VisitOutcome.RATE_LIMITED)
        if not load.loaded:
            return visit(VisitOutcome.LOAD_FAILED)

        passes = 0
        moves = s.pattern.moves()
        while passes < s.max_passes:
            missing_before_pass = after.missing_count(level)
            pass_traffic = Traffic()
            for number, move in enumerate(moves, 1):
                step = self._viewer.look(move)
                pass_traffic.add(step)
                self._observer.move_done(move, number, len(moves), step)
                if rate_limited(step):
                    return visit(VisitOutcome.RATE_LIMITED, passes + 1)
            passes += 1
            after = self._coverage.for_sweep(sweep.uuid)
            self._observer.pass_done(passes, pass_traffic, after, level)
            # Another pass only helps if this one still found tiles (e.g. slow loading).
            if after.is_complete(level) or after.missing_count(level) == missing_before_pass:
                break

        fetched = 0
        if s.fetch_unviewable and not after.is_complete(level):
            wanted = after.missing_names(level)
            step = self._viewer.fetch_tiles(sweep, wanted)
            self._observer.direct_fetch_done(len(wanted), step)
            fetched = step.saved
            if rate_limited(step):
                return visit(VisitOutcome.RATE_LIMITED, passes, fetched)
        return visit(VisitOutcome.WALKED, passes, fetched)
