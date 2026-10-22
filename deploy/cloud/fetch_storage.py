"""Download every object of a Supabase Storage bucket into a folder (used by backup.sh).

    python3 fetch_storage.py BUCKET DEST_DIR < object-names.txt

Object names come from storage.objects, one per line. Environment: SUPABASE_URL and
SUPABASE_SERVICE_KEY (the project's secret key; it reads private buckets). Standard library only.
"""

import os
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    bucket, dest = argv
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key}
    if key.startswith("eyJ"):  # legacy service_role JWT; sb_secret_ keys go in apikey only
        headers["Authorization"] = f"Bearer {key}"

    root = Path(dest).resolve()
    root.mkdir(parents=True, exist_ok=True)
    count = 0
    for line in sys.stdin:
        name = line.rstrip("\r\n")
        if not name:
            continue
        target = (root / name).resolve()
        if root not in target.parents:
            print(f"Refusing object name outside the backup folder: {name!r}", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        url = (f"{base}/storage/v1/object/authenticated/"
               f"{urllib.parse.quote(bucket, safe='')}/{urllib.parse.quote(name)}")
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as out:
            shutil.copyfileobj(response, out)
        count += 1
    print(f"{count} file(s) downloaded from bucket {bucket}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
