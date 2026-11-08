"""Saves captured model assets into a download folder without overwriting."""
from urllib.parse import unquote, urlsplit

from .layout import DownloadLayout


class AssetStore:
    """Writes responses for /models/<internal_id>/... URLs where matterport-dl would put them.

    Paths follow the download's layout (e.g. "~" stored as "_" for non-tilde
    downloads) so the local server finds them. Existing files are never
    replaced, so a walk can only add to a download.
    """

    def __init__(self, layout: DownloadLayout):
        self._layout = layout
        self._download_dir = layout.download_dir.resolve()
        self._model_prefix = f"models/{layout.internal_model_id}/"
        self._added: list[str] = []

    def relative_path(self, url: str) -> str | None:
        """Download-relative path for `url`, or None if it is not one of this model's assets."""
        path = unquote(urlsplit(url).path).lstrip("/")
        start = path.find(self._model_prefix)
        if start == -1 or path.endswith("/"):
            return None
        return self._layout.local_path(path[start:])

    @property
    def added_count(self) -> int:
        """How many files this store has written."""
        return len(self._added)

    def needs(self, url: str) -> bool:
        """True if `url` is a model asset not yet on disk (so its body is worth fetching)."""
        rel = self.relative_path(url)
        return rel is not None and not (self._download_dir / rel).exists()

    def save(self, url: str, body: bytes) -> bool:
        """Write `body` for `url` if it is a new model asset. Returns True if written."""
        rel = self.relative_path(url)
        if rel is None or not body:
            return False
        target = (self._download_dir / rel).resolve()
        if not target.is_relative_to(self._download_dir) or target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self._added.append(rel)
        return True
