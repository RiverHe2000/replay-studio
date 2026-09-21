"""Create an explicitly synthetic demonstration via the real HTTP/model pipeline."""

import argparse
import json
import os
import secrets
from pathlib import Path

from verify_e2e import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--video", type=Path, default=Path("data/fixtures/debugging/scripted_debugging.mp4"))
    parser.add_argument("--access-file", type=Path, default=Path("data/demo-access.json"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/demo"))
    parser.add_argument("--run-worker", action="store_true", help="Only when no separate worker is running")
    args = parser.parse_args()
    if args.access_file.exists():
        access = json.loads(args.access_file.read_text(encoding="utf-8"))
    else:
        access = {"email": "demo@replay.local", "password": secrets.token_urlsafe(24), "url": args.base_url}
        args.access_file.parent.mkdir(parents=True, exist_ok=True)
        with args.access_file.open("x", encoding="utf-8") as handle:
            json.dump(access, handle, indent=2)
        args.access_file.chmod(0o600)
    os.environ["REPLAY_VERIFY_EMAIL"] = access["email"]
    os.environ["REPLAY_VERIFY_PASSWORD"] = access["password"]
    args.timeout = 1800
    args.require_modalities = "asr,ocr,visual"
    result = run(args)
    print(f"Demo {result['status']}. Private login details: {args.access_file.resolve()}")
    print(f"Evidence: {(args.out / 'verification.json').resolve()}")


if __name__ == "__main__":
    main()
