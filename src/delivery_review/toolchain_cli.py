"""Download the fixed public tools and write an inspection toolchain configuration."""
import argparse
import json
from pathlib import Path
import sys

from .contracts import ContractError
from .toolchain import install_tools


def main(argv=None):
    parser = argparse.ArgumentParser(prog="delivery-review tools", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="prepare pinned tools without installing PR dependencies")
    install.add_argument("--out", required=True, type=Path, help="new directory whose parent already exists")
    args = parser.parse_args(argv)
    try:
        config = install_tools(args.out)
        print(json.dumps({"operation": "tools_install", "status": "written", "toolchain": str(config),
            "qualification": "not_established"}, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, TypeError, KeyError) as exc:
        allowed = {"unsupported_toolchain_python", "unsupported_toolchain_platform", "git_executable_unavailable",
            "operator_tool_unavailable", "tool_download_timeout", "tool_download_failed", "tool_download_digest_mismatch"}
        reason = str(exc) if isinstance(exc, ContractError) and str(exc) in allowed else (
            "output_already_exists" if isinstance(exc, FileExistsError) else "tool_install_failed")
        print(json.dumps({"operation": "tools_install", "status": "error", "reason": reason}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
