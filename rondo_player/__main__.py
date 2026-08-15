"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from rondo_player.agent import Agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Rondo Club TV player")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".config/rondo-player/config.json",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".local/state/rondo-player/state.json",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    site_url = str(config.get("site_url", "")).rstrip("/")
    parsed = urlparse(site_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("site_url moet een geldige https-URL zijn")

    Agent(site_url, args.state).run()


if __name__ == "__main__":
    main()
