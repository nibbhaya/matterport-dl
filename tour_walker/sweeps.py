"""Sweep (panorama position) catalog read from a matterport-dl download."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

SWEEPS_GRAPH_FILE = Path("api/mp/models/graph_GetShowcaseSweeps.json")
_MODEL_ID_IN_URL = re.compile(r"/models/([0-9a-f]{32})/")


@dataclass(frozen=True)
class Sweep:
    """One panorama position in a tour.

    index: the number the Showcase viewer uses in the `ss=` deep-link parameter.
    uuid:  the 32-hex id used as the tile folder name (assets/~/tiles/<uuid>/).
    label: the human-readable label shown in Showcase.
    """
    index: int
    uuid: str
    label: str


@dataclass(frozen=True)
class SweepCatalog:
    """All sweeps of one tour plus the internal model id their assets live under."""
    internal_model_id: str
    sweeps: tuple[Sweep, ...]

    @classmethod
    def from_download(cls, download_dir: Path) -> "SweepCatalog":
        """Load from downloads/<share_id>/, which matterport-dl must have populated."""
        graph_path = download_dir / SWEEPS_GRAPH_FILE
        if not graph_path.exists():
            raise FileNotFoundError(
                f"{graph_path} not found; download the tour with matterport-dl first")
        locations = json.loads(graph_path.read_text())["data"]["model"]["locations"]
        sweeps = tuple(sorted(
            (Sweep(loc["index"], loc["pano"]["sweepUuid"], loc["pano"].get("label", ""))
             for loc in locations if loc.get("pano")),
            key=lambda s: s.index))
        return cls(_find_internal_model_id(locations), sweeps)

    def select(self, indexes: set[int] | None) -> tuple[Sweep, ...]:
        """Sweeps whose index is in `indexes`, or all sweeps when it is None."""
        if indexes is None:
            return self.sweeps
        return tuple(s for s in self.sweeps if s.index in indexes)


def _find_internal_model_id(locations: list[dict]) -> str:
    for loc in locations:
        for skybox in (loc.get("pano") or {}).get("skyboxes") or []:
            match = _MODEL_ID_IN_URL.search(skybox.get("tileUrlTemplate", ""))
            if match:
                return match.group(1)
    raise ValueError("could not determine the internal model id from the sweep data")
