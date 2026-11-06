"""Read-only evidence tooling. Qualification and readiness are separate claims."""
import argparse
import sys

from . import __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="delivery-review", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("observations", help="inspect / verify / replay / refresh platform captures",
                        add_help=False)
    commands.add_parser("bundle", help="verify / replay saved-normalized evidence bundles", add_help=False)
    commands.add_parser("policy", help="seal / verify a complete inspection profile identity", add_help=False)
    # Delegate the full tail so command help/options belong to their own parser.
    if argv and argv[0] == "observations":
        from .observation_cli import main as run
        return run(argv[1:])
    if argv and argv[0] == "bundle":
        from .bundle_cli import main as run
        return run(argv[1:])
    if argv and argv[0] == "policy":
        from .policy_cli import main as run
        return run(argv[1:])
    parser.parse_args(argv)
    return 2
