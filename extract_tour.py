"""Recover Matterport tour files from Chrome's disk cache.

Saves every cached HTTP 200 response whose URL contains one of the given IDs,
using the URL path as the file path (e.g. models/<internal_id>/assets/...),
so the output matches the matterport-dl downloads/<share_id>/ layout.
404 responses and HTML error pages stored under image names are skipped.

usage:
  python3 extract_tour.py OUT_DIR KNOWN_MISSING_TXT ID[,ID...] CACHE_DIR [CACHE_DIR...]

  OUT_DIR            folder to write files into (kept separate from downloads/)
  KNOWN_MISSING_TXT  downloads/<share_id>/known_missing.txt; reports which of
                     those files the cache recovered (the file need not exist).
                     Its folder must be the download folder: run_args.json there
                     decides whether "~" in paths is written as "_" (as
                     matterport-dl does unless downloaded with --tilde)
  ID[,ID...]         share ID (from ?m=...) and internal 32-hex model ID,
                     found under downloads/<share_id>/models/
  CACHE_DIR          Chrome Cache_Data folder(s), one per profile:
                     ~/Library/Caches/Google/Chrome/<Profile>/Cache/Cache_Data

example:
  C=~/Library/Caches/Google/Chrome
  python3 extract_tour.py ~/Desktop/xb99ucgvFAH_from_cache \\
      downloads/xb99ucgvFAH/known_missing.txt \\
      xb99ucgvFAH,526e8eb5ea624fe99a68df9dedd5a9fe \\
      "$C/Default/Cache/Cache_Data" "$C/Profile 1/Cache/Cache_Data"

Then merge into the download without overwriting:
  rsync -a --ignore-existing --itemize-changes --stats OUT_DIR/ downloads/<share_id>/
  (--itemize-changes lists each new file; --stats summarises the count;
   add --dry-run to preview without copying)
"""
import hashlib
import json
import os
import re
import struct
import sys
import zlib
from collections import Counter
from urllib.parse import urlsplit, unquote

ENTRY_MAGIC = struct.pack("<Q", 0xFCFB6D1BA7725C30)
EOF_MAGIC = struct.pack("<Q", 0xF4FA6F45970D41D8)
HEADER_SIZE = 24
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def read_entry(path):
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(ENTRY_MAGIC):
        return None
    key_len = struct.unpack_from("<I", data, 12)[0]
    key = data[HEADER_SIZE:HEADER_SIZE + key_len].decode("utf-8", "replace")
    start = HEADER_SIZE + key_len
    end = data.find(EOF_MAGIC, start)
    if end == -1:
        return None
    body = data[start:end]
    # stream 0 (HTTP headers) follows the first EOF record
    headers = data[end:].lower()
    return key.split(" ")[-1], body, headers


def decode(body, headers):
    if b"content-encoding: gzip" in headers or body[:2] == b"\x1f\x8b":
        try:
            return zlib.decompress(body, 47), "ok"
        except zlib.error:
            return body, "bad-gzip"
    if b"content-encoding: br" in headers:
        return body, "brotli"
    return body, "ok"


def local_path(url, tilde):
    parts = urlsplit(url)
    rel = unquote(parts.path).lstrip("/")
    if parts.query and not rel.startswith("models/"):
        rel += "__" + hashlib.sha1(parts.query.encode()).hexdigest()[:10]
    if rel.endswith("/") or not rel:
        rel += "index"
    # Same rule as matterport-dl (and tour_walker/layout.py): without --tilde it
    # stores "~" as "_", and the local server only looks there.
    return rel if tilde else rel.replace("~", "_")


def download_uses_tilde(download_dir):
    """TILDE setting from downloads/<share_id>/run_args.json (matterport-dl's default if absent)."""
    run_args = os.path.join(download_dir, "run_args.json")
    if os.path.exists(run_args):
        with open(run_args) as f:
            return bool(json.load(f).get("TILDE", sys.platform == "win32"))
    return sys.platform == "win32"


def main():
    out_dir, missing_txt, ids, *caches = sys.argv[1:]
    ids = ids.split(",")
    tilde = download_uses_tilde(os.path.dirname(os.path.abspath(missing_txt)))
    print(f"download layout: {'~' if tilde else '_'} paths (TILDE={tilde} in run_args.json)")
    missing = set()
    if os.path.exists(missing_txt):
        missing = {l.strip() for l in open(missing_txt) if l.strip()}
    saved, stats, recovered = {}, Counter(), set()
    for cache in caches:
        for name in os.listdir(cache):
            try:
                entry = read_entry(os.path.join(cache, name))
            except OSError:
                continue
            if not entry:
                continue
            url, body, headers = entry
            if not any(i in url for i in ids):
                continue
            if not body:
                stats["empty"] += 1
                continue
            m = re.search(rb"http/1\.[01] (\d{3})", headers)
            if m and m.group(1) != b"200":
                stats["http-" + m.group(1).decode()] += 1
                continue
            body, status = decode(body, headers)
            if status != "ok":
                stats[status] += 1
                continue
            rel = local_path(url, tilde)
            if rel.lower().endswith(IMAGE_EXTS) and body.lstrip()[:1] == b"<":
                stats["html-not-image"] += 1
                continue
            if len(body) <= len(saved.get(rel, b"")):
                stats["duplicate"] += 1
                continue
            saved[rel] = body
    for rel, body in saved.items():
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(body)
        if rel in missing:
            recovered.add(rel)
    print(f"saved {len(saved)} files to {out_dir}  skipped: {dict(stats)}")
    print(f"known_missing recovered: {len(recovered)} / {len(missing)}")
    with open(os.path.join(out_dir, "recovered_missing.txt"), "w") as f:
        f.write("\n".join(sorted(recovered)))


if __name__ == "__main__":
    main()
