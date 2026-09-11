import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from .agent.core import quick_analysis
from .config.settings import ConfigurationError, configure_logging, load_settings
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(
            app_env=args.env,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        configure_logging(settings)
        missing = [file for file in args.files if not Path(file).is_file()]
        if missing:
            print(
                "Input files do not exist: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        result = quick_analysis(
            query=args.query,
            files=args.files,
            output_dir=args.output_dir,
            max_rounds=args.max_rounds,
            generate_word_report=not args.no_word_report,
            settings=settings,
        )
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
