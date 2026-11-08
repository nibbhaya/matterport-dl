"""Where matterport-dl keeps a model's files on disk.

matterport-dl replaces every "~" in a file path with "_" unless the download was
made with --tilde (the default everywhere except Windows), and the local server
then only looks in the "_" paths. Anything written into a download must follow
the same rule or the served tour will not find it.
"""
import json
import sys
from dataclasses import dataclass
from pathlib import Path

RUN_ARGS_FILE = "run_args.json"


@dataclass(frozen=True)
class DownloadLayout:
    download_dir: Path
    internal_model_id: str
    tilde: bool

    @classmethod
    def from_download(cls, download_dir: Path, internal_model_id: str) -> "DownloadLayout":
        """Read the TILDE setting the download was made with (matterport-dl's default if absent)."""
        tilde = sys.platform == "win32"
        run_args = download_dir / RUN_ARGS_FILE
        if run_args.exists():
            tilde = bool(json.loads(run_args.read_text()).get("TILDE", tilde))
        return cls(download_dir, internal_model_id, tilde)

    def local_path(self, remote_path: str) -> str:
        """Download-relative path for a remote asset path such as models/<id>/assets/~/tiles/..."""
        return remote_path if self.tilde else remote_path.replace("~", "_")

    def tiles_dir(self, sweep_uuid: str) -> Path:
        """The tile folder the local server reads for `sweep_uuid`."""
        variant = "~" if self.tilde else "_"
        return self.download_dir / "models" / self.internal_model_id / "assets" / variant / "tiles" / sweep_uuid
