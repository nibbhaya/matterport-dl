"""Drives the Matterport Showcase viewer in Chrome via Playwright.

This is the only module that knows about Playwright or the Showcase UI.
Every public step returns the Traffic it caused, so callers can see successes,
HTTP errors and rate limiting as they happen.
"""
import re
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from .moves import ViewMove
from .sweeps import Sweep
from .traffic import SweepLoad, Traffic

SHOWCASE_URL = "https://my.matterport.com/show/"
# Headless Chrome needs a software GL backend to render WebGL.
_HEADLESS_GL_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
# .../tiles/<sweep uuid>/<file>?<signed query>
_TILE_URL = re.compile(r"^(?P<folder>.*/tiles/(?P<uuid>[0-9a-f]{32})/)[^/?]+(?P<query>\?.*)?$")
# Analytics pings are irrelevant to whether tiles load, so they are not counted.
_IGNORED_HOSTS = ("events.matterport.com",)


def _is_matterport(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return (host == "matterport.com" or host.endswith(".matterport.com")) and host not in _IGNORED_HOSTS


class AssetSink(Protocol):
    def needs(self, url: str) -> bool: ...
    def save(self, url: str, body: bytes) -> bool: ...


@dataclass(frozen=True)
class ViewerSettings:
    headless: bool = True
    width: int = 1600                 # LookPattern drag sizes are calibrated for 1600x900
    height: int = 900
    device_scale_factor: int = 2      # high-DPI makes the viewer prefer 2k tiles
    load_timeout_s: float = 45.0      # max wait for a sweep to start showing tiles
    settle_quiet_s: float = 1.5       # tiles count as loaded after this long with no new ones
    settle_timeout_s: float = 12.0    # max wait after each move


class ShowcaseViewer:
    """A Chrome window showing one tour; use as a context manager."""

    def __init__(self, share_id: str, sink: AssetSink, settings: ViewerSettings = ViewerSettings()):
        self._share_id = share_id
        self._sink = sink
        self._settings = settings
        self._pending = []           # successful asset responses since the last drain
        self._traffic = Traffic()    # traffic of the step in progress
        self._last_asset_at = 0.0
        self._tile_url_by_sweep: dict[str, re.Match] = {}  # a signed tile URL seen per sweep
        self._playwright = self._browser = self._context = self._page = None

    def __enter__(self) -> "ShowcaseViewer":
        from playwright.sync_api import sync_playwright  # optional dependency
        s = self._settings
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            channel="chrome", headless=s.headless, args=_HEADLESS_GL_ARGS if s.headless else [])
        self._context = self._browser.new_context(
            viewport={"width": s.width, "height": s.height}, device_scale_factor=s.device_scale_factor)
        # Context-level, because the viewer fetches tiles from worker/xhr contexts.
        self._context.on("response", self._on_response)
        self._context.on("requestfailed", self._on_request_failed)
        self._page = self._context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def open_sweep(self, sweep: Sweep) -> SweepLoad:
        """Load the tour standing at `sweep`; `loaded` is False if no assets arrived in time."""
        self._begin_step()
        started = time.monotonic()
        # `ss` is 1-based (ss=N opens the sweep with index N-1); ss=0 means "start position".
        url = f"{SHOWCASE_URL}?m={self._share_id}&ss={sweep.index + 1}&sr=0,0&play=1&qs=1"
        self._last_asset_at = 0.0
        try:
            self._page.goto(url, wait_until="domcontentloaded")
        except Exception:  # navigation error (offline, DNS, timeout): reported as not loaded
            self._traffic.failed += 1
        deadline = started + self._settings.load_timeout_s
        while (self._last_asset_at == 0.0 and time.monotonic() < deadline
               and not self._traffic.rate_limited):
            self._page.wait_for_timeout(250)
        self._settle()
        return SweepLoad(self._last_asset_at != 0.0, time.monotonic() - started, self._end_step())

    def look(self, move: ViewMove) -> Traffic:
        """Drag from the centre of the view (and zoom), then wait for tiles."""
        self._begin_step()
        cx, cy = self._settings.width // 2, self._settings.height // 2
        mouse = self._page.mouse
        if move.drag_x or move.drag_y:
            mouse.move(cx, cy)
            mouse.down()
            mouse.move(cx + move.drag_x, cy + move.drag_y, steps=20)
            mouse.up()
        if move.zoom_in:
            mouse.move(cx, cy)
            mouse.wheel(0, -600)
        self._settle()
        if move.zoom_in:
            mouse.wheel(0, 600)  # zoom back out so the next drag covers the same angle
        return self._end_step()

    def fetch_tiles(self, sweep: Sweep, file_names: tuple[str, ...]) -> Traffic:
        """Request specific tiles of `sweep` directly, reusing the signed query of a tile
        the viewer already loaded for it. For tiles the viewer never asks for (the centre
        of the straight-up face, which its pitch limit keeps out of view).
        Nothing is requested if no tile of this sweep was seen yet (no signed query to
        reuse), and it stops at the first 429."""
        self._begin_step()
        seen = self._tile_url_by_sweep.get(sweep.uuid)
        for name in file_names if seen else ():
            url = f"{seen['folder']}{name}{seen['query'] or ''}"
            if not self._sink.needs(url):
                continue
            try:
                response = self._context.request.get(url)
            except Exception:
                self._traffic.failed += 1
                continue
            # Direct requests do not raise browser events, so count them here.
            self._record_status(response.status, response.headers)
            if response.ok and self._sink.save(url, response.body()):
                self._traffic.saved += 1
            if self._traffic.rate_limited:
                break
        return self._end_step()

    def _begin_step(self) -> None:
        self._traffic = Traffic()

    def _end_step(self) -> Traffic:
        traffic, self._traffic = self._traffic, Traffic()
        return traffic

    def _settle(self) -> None:
        """Wait until asset traffic goes quiet, then save what arrived."""
        s = self._settings
        start = time.monotonic()
        while time.monotonic() - start < s.settle_timeout_s:
            self._page.wait_for_timeout(250)
            if time.monotonic() - max(self._last_asset_at, start) >= s.settle_quiet_s:
                break
        self._drain()

    def _record_status(self, status: int, headers: dict) -> None:
        self._traffic.statuses[status] += 1
        if status == 429 and headers.get("retry-after"):
            self._traffic.retry_after = headers["retry-after"]

    def _on_response(self, response) -> None:
        # Only record here; reading bodies inside an event handler can deadlock the sync API.
        if not _is_matterport(response.url):
            return
        self._record_status(response.status, response.headers)
        if response.status == 200 and "/models/" in response.url:
            self._last_asset_at = time.monotonic()
            self._pending.append(response)
            tile = _TILE_URL.match(response.url)
            if tile:
                self._tile_url_by_sweep[tile["uuid"]] = tile

    def _on_request_failed(self, request) -> None:
        # ERR_ABORTED is the viewer cancelling tiles it no longer needs while turning: not a failure.
        if _is_matterport(request.url) and "ERR_ABORTED" not in (request.failure or ""):
            self._traffic.failed += 1

    def _drain(self) -> None:
        pending, self._pending = self._pending, []
        for response in pending:
            if not self._sink.needs(response.url):
                continue
            try:
                body = response.body()
            except Exception:  # response evicted or page navigated away
                continue
            if self._sink.save(response.url, body):
                self._traffic.saved += 1
