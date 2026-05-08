#!/usr/bin/env python3
"""Shared configuration for OogaScan framework."""

import os
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).parent / "oogascan.json"

@dataclass
class ScanConfig:
    ports: list = field(default_factory=lambda: [23, 2323])
    scan_timeout: float = 1.0
    brute_timeout: int = 20
    max_brute_parallel: int = 32
    scanner_threads: int = (os.cpu_count() or 2) * 2
    brute_threads: int = (os.cpu_count() or 2) * 2
    ring_buffer_size: int = 16384
    status_interval: int = 8
    rate_limit: int = 0
    bypass_timeout: int = 15
    honeypot_patterns: list = field(default_factory=lambda: [
        "cowrie", "honeypot", "HoneyTel", "sensor", "Decoy", "My honeypot",
        "this system is monitored", "forensics", "Kippo", "kippo", "TCP Forwarder",
    ])

@dataclass
class OogaConfig:
    scan: ScanConfig = field(default_factory=ScanConfig)
    creds_file: str = ""
    output_dir: str = "output"

    def save(self, path: Path = DEFAULT_CONFIG_PATH):
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH):
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        scan = ScanConfig(**raw.pop("scan", {}))
        return cls(scan=scan, **raw)


COMMON_CREDS = [
    ("root", "root"), ("root", "admin"), ("root", "toor"), ("root", "1234"),
    ("root", "12345"), ("root", "123456"), ("root", "password"), ("root", "default"),
    ("admin", "admin"), ("admin", "1234"), ("admin", "password"), ("admin", "default"),
    ("user", "user"), ("user", "1234"), ("guest", "guest"), ("guest", "default"),
    ("support", "support"), ("default", "default"), ("pi", "raspberry"),
    ("test", "test"), ("root", "qwerty"), ("root", "abc123"), ("root", "1"),
    ("root", "1111"), ("admin", ""),  ("root", ""),
    ("root", "vizxv"), ("root", "xc3511"), ("root", "Zte521"),
    ("root", "juantech"), ("root", "realtek"), ("admin", "smcadmin"),
    ("root", "dreambox"), ("root", "hi3518"), ("root", "xmhdipc"),
]

def load_creds_file(path: str) -> list:
    creds = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":", 1)
            if len(parts) == 2:
                creds.append((parts[0], parts[1]))
    return creds
