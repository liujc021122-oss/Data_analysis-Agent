import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence
from uuid import UUID

from .agent.core import quick_analysis
from .config.settings import ConfigurationError, configure_logging, load_settings
from .datasets import DatasetResolver, LocalStorageBackend, UnitOfWorkDatasetStore
from .persistence.database import Database
from .persistence.unit_of_work import UnitOfWork
from .services.errors import sanitize_exception


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-analysis-agent",
        description="Run the data analysis agent against input files"
    )
    parser.add_argument("files", nargs="*", help="CSV or other input data files")
    parser.add_argument("--query", default="分析输入数据并生成关键发现和图表")
    parser.add_argument(
        "--env", choices=("development", "test", "production"), default=None
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--no-word-report", action="store_true")
    parser.add_argument("--dataset-id", action="append", default=None)
    parser.add_argument("--dataset-owner-id", type=UUID, default=None)
    return parser


def _validate_dataset_storage(settings) -> None:
    if settings.app_env == "production":
        raise ConfigurationError(
            "production dataset-id mode requires an object storage adapter; "
            "no object storage adapter is configured"
        )


def build_dataset_resolver(database: Database, settings) -> DatasetResolver:
    _validate_dataset_storage(settings)
    storage = LocalStorageBackend(settings.storage_local_root)
    metadata_store = UnitOfWorkDatasetStore(
        lambda: UnitOfWork(database.session_factory)
    )
    return DatasetResolver(storage=storage, metadata_store=metadata_store)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        requested_output_dir = (
            args.output_dir if args.output_dir and args.output_dir.strip() else None
        )
        settings = load_settings(
            app_env=args.env,
            output_dir=Path(requested_output_dir) if requested_output_dir else None,
        )
        configure_logging(settings)
        if args.files and args.dataset_id:
            print(
                "Positional files cannot be combined with --dataset-id",
                file=sys.stderr,
            )
            return 2
        if args.dataset_id and args.dataset_owner_id is None:
            print(
                "Dataset mode requires --dataset-owner-id",
                file=sys.stderr,
            )
            return 2
        if args.dataset_id and not settings.database_url:
            print(
                "Dataset mode requires DATABASE_URL",
                file=sys.stderr,
            )
            return 2
        missing = [file for file in args.files if not Path(file).is_file()]
        if missing:
            print(
                "Input files do not exist: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        database = None
        try:
            dataset_resolver = None
            if args.dataset_id:
                _validate_dataset_storage(settings)
                database = Database.from_settings(settings)
                dataset_resolver = build_dataset_resolver(database, settings)
            result = quick_analysis(
                query=args.query,
                files=args.files if not args.dataset_id else None,
                dataset_ids=args.dataset_id,
                output_dir=requested_output_dir or settings.output_dir,
                max_rounds=args.max_rounds,
                generate_word_report=not args.no_word_report,
                settings=settings,
                dataset_resolver=dataset_resolver,
                dataset_owner_id=args.dataset_owner_id,
            )
        finally:
            if database is not None:
                database.engine.dispose()
        print(result)
        return 0
    except ConfigurationError as exc:
        print(
            "Configuration error: "
            + sanitize_exception(exc),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        logging.getLogger("data_analysis_agent").error(
            "Analysis failed: %s",
            sanitize_exception(exc, include_message=False),
        )
        return 1
