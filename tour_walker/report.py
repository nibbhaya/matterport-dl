"""Console progress output and the end-of-walk summary."""
import time
from typing import TextIO

from .coverage import EXPECTED_TILES, SweepCoverage, TileLevel
from .moves import ViewMove
from .sweeps import Sweep
from .traffic import FORBIDDEN, SweepLoad, Traffic
from .walker import SweepVisit, VisitOutcome, WalkResult

_INDENT = " " * 11


def describe_traffic(traffic: Traffic) -> str:
    """e.g. 'saved 12, 140 ok | HTTP 404x3, 2 network failures'."""
    text = f"saved {traffic.saved}, {traffic.ok} ok"
    problems = [f"HTTP " + ", ".join(f"{status}x{n}" for status, n in traffic.errors.items())] if traffic.errors else []
    if traffic.failed:
        problems.append(f"{traffic.failed} network failure{'s' if traffic.failed > 1 else ''}")
    return text + (" | " + ", ".join(problems) if problems else "")


def _is_alarming(traffic: Traffic) -> bool:
    """Rate limiting, blocking, server errors or network failures: worth a line of its own."""
    return traffic.failed > 0 or any(status in (403, 429) or status >= 500 for status in traffic.errors)


def _describe_move(move: ViewMove) -> str:
    if move.drag_y:
        return "tilt " + ("down" if move.drag_y < 0 else "up")
    return "zoom" if move.zoom_in and not move.drag_x else "turn"


class ConsoleReporter:
    """Prints walk progress as it happens. Implements walker.WalkObserver."""

    def __init__(self, out: TextIO, verbose: bool = False):
        self._out = out
        self._verbose = verbose
        self._header = ""

    def sweep_started(self, sweep: Sweep, number: int, total: int) -> None:
        self._header = f"[{number}/{total}] sweep {sweep.index:>3} ({sweep.uuid[:8]})"

    def sweep_loaded(self, sweep: Sweep, load: SweepLoad) -> None:
        self._line(f"{self._header}: opening")
        state = f"loaded in {load.seconds:.1f}s" if load.loaded else f"NOT loaded after {load.seconds:.0f}s"
        self._detail(f"{state}: {describe_traffic(load.traffic)}")
        self._warn_about(load.traffic)

    def move_done(self, move: ViewMove, number: int, total: int, traffic: Traffic) -> None:
        if self._verbose:
            self._detail(f"  move {number:>2}/{total} {_describe_move(move):<9} {describe_traffic(traffic)}")
        elif _is_alarming(traffic):  # plain 404s are summarised in the pass line instead
            self._detail(f"  move {number}/{total}: {describe_traffic(traffic)}")
        self._warn_about(traffic)

    def pass_done(self, pass_number: int, traffic: Traffic, coverage: SweepCoverage, level: TileLevel) -> None:
        self._detail(f"pass {pass_number}: {describe_traffic(traffic)} -> "
                     f"{coverage.present_count(level)}/{EXPECTED_TILES[level]} {level} tiles")

    def direct_fetch_done(self, requested: int, traffic: Traffic) -> None:
        self._detail(f"direct fetch of {requested} unviewable tile(s): {describe_traffic(traffic)}")
        self._warn_about(traffic)

    def sweep_done(self, visit: SweepVisit, number: int, total: int) -> None:
        level, want = visit.level, EXPECTED_TILES[visit.level]
        have = visit.after.present_count(level)
        if visit.outcome is VisitOutcome.SKIPPED_COMPLETE:
            self._line(f"{self._header}: already complete, skipped")
        elif visit.outcome is VisitOutcome.LOAD_FAILED:
            self._detail(f"FAILED: viewer did not load this sweep ({have}/{want} {level} tiles)")
        elif visit.outcome is VisitOutcome.RATE_LIMITED:
            wait = f" Matterport asks to wait {visit.traffic.retry_after}s." if visit.traffic.retry_after else ""
            self._detail(f"STOPPED: rate limited by Matterport (HTTP 429).{wait} "
                         f"+{visit.gained()} tiles kept, now {have}/{want}.")
        else:
            status = "OK" if have == want else f"INCOMPLETE ({want - have} missing)"
            self._detail(f"{status}: +{visit.gained()} {level} tiles, now {have}/{want}")

    def _warn_about(self, traffic: Traffic) -> None:
        if traffic.statuses[FORBIDDEN]:
            self._detail("WARNING: HTTP 403 Forbidden from Matterport (blocked, or a signed link expired)")

    def _line(self, text: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {text}", file=self._out, flush=True)

    def _detail(self, text: str) -> None:
        print(f"{_INDENT}{text}", file=self._out, flush=True)


def summary(result: WalkResult, files_added: int) -> str:
    level, want = result.target_level, EXPECTED_TILES[result.target_level]
    visits = result.visits
    count = lambda outcome: sum(v.outcome is outcome for v in visits)
    incomplete = [v for v in visits if not v.after.is_complete(level)]
    lines = [
        "",
        f"Visited {len(visits)} sweeps: {count(VisitOutcome.WALKED)} walked, "
        f"{count(VisitOutcome.SKIPPED_COMPLETE)} already complete, {count(VisitOutcome.LOAD_FAILED)} failed to load, "
        f"{count(VisitOutcome.RATE_LIMITED)} rate limited.",
        f"Files added to the download: {files_added} ({sum(v.gained() for v in visits)} of them {level} tiles).",
        f"Matterport traffic: {describe_traffic(result.traffic)}.",
        f"Sweeps with all {want} {level} tiles: {len(visits) - len(incomplete)}/{len(visits)}.",
    ]
    if result.stopped_for_rate_limit:
        lines.append("Stopped early because Matterport is rate limiting (HTTP 429). Wait (often about an hour), "
                     "then run the same command again; finished sweeps are skipped.")
    if incomplete:
        lines.append(f"Still missing {sum(v.after.missing_count(level) for v in incomplete)} {level} tiles:")
        lines += [f"  sweep {v.sweep.index:>3} ({v.sweep.uuid}): {v.after.missing_count(level)} missing"
                  for v in sorted(incomplete, key=lambda v: v.after.missing_count(level))]
    return "\n".join(lines)
