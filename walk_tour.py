"""Walk a downloaded Matterport tour in Chrome to collect missing panorama tiles.

Opens each sweep on my.matterport.com, looks all the way around (and straight
up and down) so the viewer requests every tile, and saves new model assets into
downloads/<share_id>/ without overwriting anything. Sweeps that already have
every tile at the target resolution are skipped.

Requires: pip install -r requirements-walker.txt  (and Google Chrome installed)

examples:
  python3 walk_tour.py xb99ucgvFAH
  python3 walk_tour.py xb99ucgvFAH --sweeps 4,41,63 --headed -v

Progress shows each sweep's load time, each look-around pass, HTTP errors and
network failures as they happen. If Matterport rate limits (HTTP 429) the walk
stops (exit code 75); wait and re-run, finished sweeps are skipped.
"""
import argparse
import sys
from pathlib import Path

from tour_walker.asset_store import AssetStore
from tour_walker.coverage import EXPECTED_TILES, TileCoverage
from tour_walker.layout import DownloadLayout
from tour_walker.report import ConsoleReporter, summary
from tour_walker.sweeps import SweepCatalog
from tour_walker.viewer import ShowcaseViewer, ViewerSettings
from tour_walker.walker import TourWalker, WalkSettings


EXIT_RATE_LIMITED = 75  # EX_TEMPFAIL: try again later


def parse_sweep_indexes(text: str) -> set[int]:
    try:
        return {int(part) for part in text.split(",") if part.strip()}
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected comma-separated numbers, got {text!r}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("share_id", help="tour ID from the ?m= part of the URL (= download folder name)")
    p.add_argument("--base-folder", type=Path, default=Path(__file__).parent / "downloads",
                   help="folder holding downloads (default: ./downloads)")
    p.add_argument("--sweeps", type=parse_sweep_indexes,
                   help="comma-separated sweep indexes to visit (default: all)")
    p.add_argument("--level", choices=EXPECTED_TILES, default="2k",
                   help="tile resolution to complete (default: 2k)")
    p.add_argument("--passes", type=int, default=2,
                   help="max look-around passes per incomplete sweep (default: 2)")
    p.add_argument("--include-complete", action="store_true",
                   help="also visit sweeps that already have every tile")
    p.add_argument("--no-fetch-unviewable", dest="fetch_unviewable", action="store_false",
                   help="only save tiles the viewer itself loads; don't request the few it "
                        "never shows (centre of the straight-up face) directly")
    p.add_argument("--headed", action="store_true", help="show the Chrome window")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print a line for every move (default: only passes, errors and results)")
    p.add_argument("--keep-going", dest="stop_on_rate_limit", action="store_false",
                   help="carry on after Matterport rate limits (HTTP 429) instead of stopping")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    download_dir = args.base_folder / args.share_id
    catalog = SweepCatalog.from_download(download_dir)
    sweeps = catalog.select(args.sweeps)
    unknown = sorted((args.sweeps or set()) - {s.index for s in sweeps})
    if unknown:
        print(f"warning: no sweep with index {', '.join(map(str, unknown))} "
              f"(valid: 0-{catalog.sweeps[-1].index})", file=sys.stderr)
    if not sweeps:
        return 1

    layout = DownloadLayout.from_download(download_dir, catalog.internal_model_id)
    store = AssetStore(layout)
    coverage = TileCoverage(layout)
    settings = WalkSettings(target_level=args.level, max_passes=args.passes,
                            include_complete=args.include_complete, fetch_unviewable=args.fetch_unviewable,
                            stop_on_rate_limit=args.stop_on_rate_limit)
    reporter = ConsoleReporter(sys.stdout, verbose=args.verbose)

    print(f"Walking {len(sweeps)} sweeps of {args.share_id} (model {catalog.internal_model_id}, "
          f"tiles in {layout.tiles_dir('<sweep>').parent.relative_to(download_dir)})")
    try:
        with ShowcaseViewer(args.share_id, store, ViewerSettings(headless=not args.headed)) as viewer:
            result = TourWalker(viewer, coverage, settings, reporter).walk(sweeps)
    except KeyboardInterrupt:
        print(f"\nInterrupted. {store.added_count} files were added before stopping.")
        return 130
    print(summary(result, store.added_count))
    return EXIT_RATE_LIMITED if result.stopped_for_rate_limit else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
