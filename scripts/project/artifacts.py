"""Create one managed output directory for a development or release task."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.common.paths import PROJECT_ROOT, create_artifact_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("tasks", "releases", "git", "maintenance"), required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--feature")
    parser.add_argument("--version")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    try:
        print(create_artifact_run(args.kind, args.subject, feature=args.feature,
                                  version=args.version, root=args.root))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
