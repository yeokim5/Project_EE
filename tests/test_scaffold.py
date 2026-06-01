from earnings_extractor import __version__
from earnings_extractor.cli import build_parser


def test_package_has_version() -> None:
    assert __version__


def test_cli_help_parses_without_command() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert args.command is None
