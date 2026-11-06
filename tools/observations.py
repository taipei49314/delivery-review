"""Source-checkout compatibility entry point for platform observations."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from delivery_review.observation_cli import main as _main
from delivery_review.provenance import collector_identity


def main(argv=None):
    return _main(argv, identity_provider=collector_identity)


if __name__ == "__main__":
    sys.exit(main())
