"""What happened on the network during one step of a walk."""
from collections import Counter
from dataclasses import dataclass, field

RATE_LIMITED = 429
FORBIDDEN = 403


@dataclass
class Traffic:
    """Matterport responses seen during one step (opening a sweep, one move, a direct fetch).

    statuses: HTTP status -> count, for requests to Matterport hosts (analytics excluded).
    saved:    new files written to the download.
    failed:   requests that got no HTTP response at all (network errors, timeouts).
    retry_after: the server's Retry-After header from a 429, if it sent one.
    """
    statuses: Counter = field(default_factory=Counter)
    saved: int = 0
    failed: int = 0
    retry_after: str | None = None

    @property
    def rate_limited(self) -> bool:
        return self.statuses[RATE_LIMITED] > 0

    @property
    def errors(self) -> dict[int, int]:
        """HTTP error statuses (>= 400) and their counts."""
        return {status: n for status, n in sorted(self.statuses.items()) if status >= 400}

    @property
    def ok(self) -> int:
        return sum(n for status, n in self.statuses.items() if status < 400)

    def add(self, other: "Traffic") -> None:
        self.statuses.update(other.statuses)
        self.saved += other.saved
        self.failed += other.failed
        self.retry_after = other.retry_after or self.retry_after


@dataclass(frozen=True)
class SweepLoad:
    """Result of opening a sweep in the viewer."""
    loaded: bool
    seconds: float
    traffic: Traffic
