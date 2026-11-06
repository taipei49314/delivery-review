"""Source-checkout compatibility entry point for inspection policy identity."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from delivery_review.policy_cli import main

if __name__ == "__main__":
    sys.exit(main())
