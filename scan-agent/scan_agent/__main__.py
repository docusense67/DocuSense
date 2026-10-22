"""Run one scan session against the backend.

    python -m scan_agent --server https://registrar.school.edu.ph --key $STATION_KEY --folder ./drop
    python -m scan_agent --source sane              # one stack straight from the ADF scanner
    python -m scan_agent camera-test --out ./camtest  # one camera capture + quality check
    python -m scan_agent intake --hardware manual --once  # intake station (scan_agent/intake.py)
    python -m scan_agent intake-demo --server URL --key KEY  # SAMPLE pages, real screening
                                                             # (scan_agent/intake_demo.py)

Incoming-document screening (no server needed): `python -m scan_agent screen <image>`,
`screening-enroll`, `screening-eval`, `screening-calibrate` (scan_agent/screening/cli.py).

Station service (production): `--watch` with `--folder` (the scanner's own software saves
there) or `--source sane --folder <spool>` (the agent feeds the scanner itself, see
scan_agent/station.py).

The operator first picks Single or Bulk on the dashboard (that opens the session). The agent
then captures, splits, extracts, and submits each document; results appear on the dashboard.
Images live only in memory here. In file-drop mode, pass --delete-after-upload on the real
station so the scanner's output files are removed once uploaded (Section 18).
"""

import argparse
import logging
import os
import signal
import sys

from scan_agent.capture.base import PageSource, ScannerError
from scan_agent.capture.filedrop import FolderSource
from scan_agent.client import BackendClient
from scan_agent.pipeline import process_stack
from scan_agent.station import ScannerStation
from scan_agent.watch import Watcher

COLORS = {"VALID": "\033[92m", "INVALID": "\033[91m", "NEEDS_REVIEW": "\033[93m"}


def sane_options(items: list[str]) -> dict[str, str]:
    options = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--sane-option needs NAME=VALUE, got {item!r}")
        options[name.strip()] = value.strip()
    return options


def build_source(args: argparse.Namespace) -> PageSource:
    if args.source == "sane":
        from scan_agent.capture.sane import SaneSource

        return SaneSource(args.device, options=sane_options(args.sane_option))
    if args.folder:
        return FolderSource(args.folder)
    raise SystemExit("No page source: pass --folder (file-drop mode) or --source sane.")


def _stop(signum, frame):
    raise KeyboardInterrupt  # systemd stops with SIGTERM: report Offline before exiting


SCREENING_COMMANDS = ("screen", "screening-enroll", "screening-eval", "screening-calibrate")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["camera-test"]:  # needs no server: python -m scan_agent camera-test -h
        from scan_agent.camera_test import main as camera_test

        return camera_test(argv[1:])
    if argv[:1] == ["intake"]:  # feeder + camera + gate station: python -m scan_agent intake -h
        from scan_agent.intake import main as intake_main

        return intake_main(argv[1:])
    if argv[:1] == ["intake-demo"]:  # demo driver: python -m scan_agent intake-demo -h
        from scan_agent.intake_demo import main as intake_demo_main

        return intake_demo_main(argv[1:])
    if argv and argv[0] in SCREENING_COMMANDS:  # incoming-document screening tools
        from scan_agent.screening.cli import main as screening_main

        return screening_main(argv)
    parser = argparse.ArgumentParser(prog="scan_agent")
    parser.add_argument("--server", default=os.environ.get("ADGVS_SERVER"))
    parser.add_argument("--key", default=os.environ.get("ADGVS_STATION_KEY"))
    parser.add_argument("--ca-cert", default=os.environ.get("ADGVS_CA_CERT"),
                        help="PEM file to trust for the server's HTTPS certificate "
                             "(school CA or self-signed); default: public CAs")
    parser.add_argument("--source", choices=("folder", "sane"),
                        default=os.environ.get("ADGVS_SOURCE", "folder"),
                        help="folder: read files from --folder; sane: feed the ADF scanner "
                             "directly (Linux station)")
    parser.add_argument("--device", default=os.environ.get("ADGVS_SANE_DEVICE") or None,
                        help="sane: device name from `scanimage -L` (default: first "
                             "sheet-fed scanner)")
    parser.add_argument("--sane-option", action="append", metavar="NAME=VALUE",
                        default=os.environ.get("ADGVS_SANE_OPTIONS", "").split(),
                        help="sane: extra scanner option, e.g. df-action=Stop (repeatable; "
                             "ADGVS_SANE_OPTIONS holds space-separated pairs)")
    parser.add_argument("--folder", default=os.environ.get("ADGVS_SCAN_FOLDER") or None,
                        help="file-drop mode: images or PDFs, in stack order; "
                             "--source sane --watch: the spool folder")
    parser.add_argument("--delete-after-upload", action="store_true",
                        help="file-drop mode: delete the input files once every document in "
                             "the batch was uploaded (use on the production station)")
    parser.add_argument("--start-sequence", type=int, default=1,
                        help="resume a batch after a jam from this sheet number")
    parser.add_argument("--watch", action="store_true",
                        help="station service: keep watching --folder, report status, and "
                             "upload each stack the scanner saves (see scan_agent/watch.py)")
    parser.add_argument("--settle", type=float, default=5.0,
                        help="watch mode: seconds without new files before a stack is complete")
    parser.add_argument("--heartbeat", type=float, default=15.0,
                        help="watch mode: seconds between status reports to the dashboard")
    parser.add_argument("--poll", type=float, default=2.0,
                        help="--source sane --watch: seconds between feeder/session checks")
    args = parser.parse_args(argv)
    if not args.server or not args.key:
        parser.error("--server and --key (or ADGVS_SERVER / ADGVS_STATION_KEY) are required")

    client = BackendClient(args.server, args.key, ca_cert=args.ca_cert)
    if args.watch:
        if not args.folder:
            parser.error("--watch needs --folder (the scanner's output folder)")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        signal.signal(signal.SIGTERM, _stop)
        if args.source == "sane":
            ScannerStation(client, build_source(args), args.folder,
                           delete_after_upload=args.delete_after_upload,
                           heartbeat=args.heartbeat).run_forever(args.poll)
        else:
            Watcher(client, args.folder, delete_after_upload=args.delete_after_upload,
                    settle=args.settle, heartbeat=args.heartbeat).run_forever()
        return 0

    session = client.session()
    source = build_source(args)
    client.status("scanning")
    try:
        documents = process_stack(source.pages(args.start_sequence), client.lookup)
    except ScannerError as e:
        client.status(e.state.value, str(e))
        if isinstance(source, FolderSource):
            print(f"Scanner stopped: {e}. Re-run with --start-sequence {e.next_sequence}.")
        else:  # the sheets already fed are not kept in this one-shot mode
            print(f"Scanner stopped: {e}. Nothing was uploaded: clear the scanner and scan "
                  "the whole stack again (the --watch service resumes mid-stack instead).")
        return 3

    print(f"Session {session['session_id']} ({session['mode']}): {len(documents)} document(s)")
    for document in documents:
        result = client.submit(session["session_id"], document)
        color = COLORS.get(result["result"], "")
        print(f"  #{document.position:>2} {document.payload.get('document_number_read') or '?':<18}"
              f" {color}{result['result']}\033[0m  {result['reasons'][0]}")
    if args.delete_after_upload and isinstance(source, FolderSource):
        print(f"Deleted {source.delete_consumed()} uploaded file(s) from {args.folder}")
    client.status("ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
