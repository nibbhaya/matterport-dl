"""Viewer-independent description of how to look around a sweep."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ViewMove:
    """One step of looking around: drag the view, optionally zoom in, then wait for tiles.

    Drags are in screen pixels of the viewer's viewport (see LookPattern for the
    calibration and ViewerSettings for the viewport size they assume).
    """
    drag_x: int = 0
    drag_y: int = 0
    zoom_in: bool = False


@dataclass(frozen=True)
class LookPattern:
    """How to look around one sweep so every cube face tile is requested.

    Calibrated on the default 1600x900 viewport: a 500 px drag turns about 60
    degrees, so 6 drags make a full turn. Rings are taken level, tilted one way
    and tilted the other way; tilts are relative deltas, hence -400 then +800.
    This loads 92 of 96 2k tiles; the viewer's pitch limit keeps the centre 4
    tiles of the straight-up face out of view, so the walker fetches those
    directly afterwards (WalkSettings.fetch_unviewable).
    """
    yaw_drag_px: int = -500
    yaw_steps: int = 6
    ring_tilt_deltas_px: tuple[int, ...] = (0, -400, 800)

    def moves(self) -> list[ViewMove]:
        moves: list[ViewMove] = []
        for tilt in self.ring_tilt_deltas_px:
            if tilt:
                moves.append(ViewMove(drag_y=tilt))
            moves.extend(ViewMove(drag_x=self.yaw_drag_px) for _ in range(self.yaw_steps))
        return moves
