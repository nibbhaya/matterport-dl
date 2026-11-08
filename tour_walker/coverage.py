"""How many panorama tiles of each resolution a download holds per sweep."""
from dataclasses import dataclass
from typing import Literal

from .layout import DownloadLayout

TileLevel = Literal["2k", "1k", "512"]

CUBE_FACES = 6
# Each cube face is split into an n x n grid of tiles at each resolution.
GRID_SIZE: dict[TileLevel, int] = {"2k": 4, "1k": 2, "512": 1}
EXPECTED_TILES: dict[TileLevel, int] = {lvl: CUBE_FACES * n * n for lvl, n in GRID_SIZE.items()}


def tile_names(level: TileLevel) -> list[str]:
    """Every tile file name of a full panorama at `level`, e.g. 2k_face0_0_0.jpg."""
    n = GRID_SIZE[level]
    return [f"{level}_face{f}_{x}_{y}.jpg" for f in range(CUBE_FACES) for x in range(n) for y in range(n)]


@dataclass(frozen=True)
class SweepCoverage:
    """Which tiles of one sweep are missing; counts are derived from that alone."""
    sweep_uuid: str
    _missing: dict[str, tuple[str, ...]]

    def missing_names(self, level: TileLevel) -> tuple[str, ...]:
        return self._missing[level]

    def missing_count(self, level: TileLevel) -> int:
        return len(self._missing[level])

    def present_count(self, level: TileLevel) -> int:
        return EXPECTED_TILES[level] - self.missing_count(level)

    def is_complete(self, level: TileLevel) -> bool:
        return self.missing_count(level) == 0


class TileCoverage:
    """Reads tile presence from the tile folder the local server actually serves.

    Only that folder counts: a tile sitting in the other ("~" vs "_") variant is
    invisible to the served tour, so counting it would report false completeness.
    """

    def __init__(self, layout: DownloadLayout):
        self._layout = layout

    def for_sweep(self, sweep_uuid: str) -> SweepCoverage:
        folder = self._layout.tiles_dir(sweep_uuid)
        names = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
        missing = {level: tuple(n for n in tile_names(level) if n not in names) for level in GRID_SIZE}
        return SweepCoverage(sweep_uuid, missing)
