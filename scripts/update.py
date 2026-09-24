"""CLI for updating dashboard data."""

import argparse
import logging
from pathlib import Path

from modules.config import CONFIGS, CONFIG_BY_ID
from modules.github import GitHub
from modules.updater import GithubUpdater, LocalUpdater


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update dashboard data from GitHub artifacts or local results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Available configs:\n  " + "\n  ".join(CONFIG_BY_ID),
    )
    parser.add_argument("--token", help="GitHub personal access token", required=True)
    parser.add_argument("--logging-level", help="Logging level", default="INFO")
    parser.add_argument(
        "--page-limit", help="Search commit page limit", type=int, default=3
    )
    parser.add_argument(
        "--config",
        metavar="ID",
        nargs="+",
        choices=CONFIG_BY_ID,
        help="Configurations to update (default: all)",
    )
    parser.add_argument(
        "--local",
        type=Path,
        help="Path to local data dir or file (requires exactly one --config)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.logging_level))
    selected = (
        CONFIGS
        if args.config is None
        else [CONFIG_BY_ID[config_id] for config_id in args.config]
    )
    if args.local is not None and len(selected) != 1:
        parser.error("--local requires exactly one --config")

    gh = GitHub(args.token)
    for config in selected:
        logging.info("Updating %s from %s", config.id, args.local or "GitHub artifacts")
        if args.local is not None:
            LocalUpdater(gh, config, args.local).run()
        else:
            GithubUpdater(gh, config, args.page_limit).run()


if __name__ == "__main__":
    main()
