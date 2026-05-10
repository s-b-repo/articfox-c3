#!/usr/bin/env python3
"""
OogaScan — Zero-Width Dead-Drop C2 Framework

Unified entry point for all components:
  c2        — GitHub/GitLab dead-drop C2 (PasteBomb)
  control   — Operator control tool (push commands to repos)
  api       — REST API for admin & lints dashboards
"""

import sys
import argparse

if sys.version_info < (3, 10):
    print("Error: Python 3.10+ required")
    sys.exit(1)

BANNER = """\033[1;36m
   ██████   ██████   ██████   █████  ███████  ██████  █████  ███    ██
  ██    ██ ██    ██ ██       ██   ██ ██      ██      ██   ██ ████   ██
  ██    ██ ██    ██ ██   ███ ███████ ███████ ██      ███████ ██ ██  ██
  ██    ██ ██    ██ ██    ██ ██   ██      ██ ██      ██   ██ ██  ██ ██
   ██████   ██████   ██████  ██   ██ ███████  ██████ ██   ██ ██   ████

                  Zero-Width Dead-Drop C2 Framework
\033[0m"""


def main():
    parser = argparse.ArgumentParser(
        description="OogaScan — Zero-Width Dead-Drop C2 Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
modules:
  c2         GitHub/GitLab dead-drop C2 (agent/server)
  control    Operator tool — push commands to repos
  api        REST API for admin & lints dashboards

examples:
  %(prog)s c2 agent -r user/repo1 -r gl:user/backup-repo
  %(prog)s c2 server
  %(prog)s control
  %(prog)s api
""",
    )
    parser.add_argument("module", nargs="?", default="c2",
                        choices=["c2", "control", "api"],
                        help="Module to run (default: c2)")

    args, remaining = parser.parse_known_args()

    print(BANNER)

    if args.module == "c2":
        sys.argv = [sys.argv[0]] + remaining
        from pastebomb import main as pb_main
        pb_main()

    elif args.module == "control":
        sys.argv = [sys.argv[0]] + remaining
        from control import main as ctrl_main
        ctrl_main()

    elif args.module == "api":
        sys.argv = [sys.argv[0]] + remaining
        from api import main as api_main
        api_main()


if __name__ == "__main__":
    main()
