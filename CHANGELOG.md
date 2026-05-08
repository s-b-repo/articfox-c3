# Changelog

## [3.0.0] - 2026-05-04

### Added
- **Zero-width Unicode C2 protocol** — commands encoded invisibly in README files
- **Operator control tool** (`control.py`) — interactive shell for managing dead-drops
- **Debian paste support** (`dp:paste_id` prefix) — paste.debian.net as additional dead-drop
- **1MB ZW padding** — optional 1MB of random zero-width noise after payload (`--pad` / `pad` toggle)
- **Cron job persistence** — re-runs agent every 2 weeks if killed (disguised cron entry)
- **Bot ID camouflage** — stored in hidden files named after top 10 Linux tools (systemd, cron, bash, sshd, etc.)
- **Randomized padded bot hash** — random prefix added to bot hash before each heartbeat
- **Async parallel honeypot detection** — if 11+ ports open on target, skip as honeypot
- **Batch honeypot scanning** — async checks multiple IPs in parallel before deployment
- **Heartbeat via open redirect** — URL-encoded tracking endpoint through redirect server
- **Dynamic repo discovery** — agent learns new GitHub/GitLab/Debian sources from payload
- **404 pre-check with random failover** — HEAD check before fetch, randomized repo order
- `zwenc.py` — zero-width Unicode codec module (encode/decode/inject/extract/pad)
- `control.py` — operator tool with GitHub/GitLab/Debian API push

### Changed
- Agent now prioritizes zero-width encoded payloads over legacy marker tags
- Failover order is randomized each poll cycle (not sequential)
- `deploy_from_file` runs async batch honeypot scan before deploying
- `install_persistence` now installs both autostart symlink and cron job on Linux
- Heartbeat sends padded hash (random prefix + SHA256) instead of raw bot ID

## [2.0.0] - 2026-05-04

### Added
- GitLab dead-drop support (`gl:owner/repo` prefix)
- Mix-and-match GitHub + GitLab repos with automatic fallback
- `deploy` mode in pastebomb — push agent to telnet targets via brute-forced credentials
- `--daemon` flag for background agent operation
- Platform field in repo config (`"platform": "github"` or `"gitlab"`)
- GitLab raw URL and API fetching

### Removed
- ICMP-based C2 server (`c2.py`) — deleted
- ICMP-based agent (`agent.py`) — deleted
- `C2Config` class from shared config
- All raw socket / ICMP dependencies

### Changed
- C2 architecture now exclusively uses GitHub/GitLab README dead-drops
- `pastebomb.py` is now the sole C2 module (agent, server, deployer)
- `oogascan.py` entry point updated — `c2` subcommand routes to pastebomb
- `oogascan.json` simplified (removed ICMP C2 section)
- `pb_config.json` now includes `platform` field per repo

### Preserved
- Full telnet scanner & brute-forcer (`csan.py`)
- CVE-2026-24061 auth bypass
- Honeypot detection
- All command types (shell, download, dos, popmsg, add_repo, set_interval, sleep, die)
- Cross-platform persistence (Windows, Linux, macOS)
- Multiple command encodings (marker tags, code blocks, base64)
- Exponential backoff on repo failures
- Telnet deployment to compromised targets

## [1.0.0] - 2026-04-20

### Initial release
- ICMP-based C2 server and agent
- Telnet scanner & brute-forcer
- GitHub README dead-drop C2 (PasteBomb)
- Common credential list
- Honeypot detection
- CVE-2026-24061 auth bypass
