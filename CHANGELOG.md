# Changelog

## [3.1.0] - 2026-05-08

### Security Fixes
- **Timing attack on API tokens** — `api.py` token comparison now uses `hmac.compare_digest()` instead of `==` to prevent byte-by-byte timing oracle attacks
- **HTML injection in `popmsg`** — `pop_message()` now HTML-escapes input via `html.escape()` before writing to tempfile
- **Shell injection in `dos` command** — switched from `shell=True` string interpolation to `subprocess.Popen` with argument list
- **Debug mode exposure** — `api.py --debug` on non-localhost is now forced to `127.0.0.1` to prevent Werkzeug debugger RCE
- **Plaintext token configs excluded from git** — added `api_config.json`, `control_config.json`, `pb_config.json` to `.gitignore`
- **Atomic bot persistence** — `_save_bots()` now writes to a `.tmp` file and uses `os.replace()` for crash-safe updates

### Bug Fixes
- **Backoff logic for dead repos** — repos that never succeeded had `fail_count` reset every cycle due to `last_success=0.0` always passing the time check. Added `last_fail` field to `RepoSource`; backoff now uses last failure timestamp and no longer resets `fail_count` on retry
- **Double HTTP request per poll** — removed redundant `_check_404()` HEAD pre-check from `fetch_with_fallback()`; the GET fetch itself handles failures
- **Negative index in control shell** — `rm`, `push`, and `pull` commands now reject `idx < 0` instead of silently operating on the wrong element via Python's negative indexing
- **Credential sharding overflow** — brute worker count now capped to `min(threads, len(creds))` to prevent workers with empty shards falling back to the full credential list
- **Deferred API config loading** — `api.py` no longer loads configs and writes `api_config.json` at import time; initialization deferred to `_init_app()` via `@app.before_request`
- **Daemon stdin redirect** — `daemonize()` now redirects stdin to `/dev/null` alongside stdout/stderr, preventing hangs on accidental reads
- **Bot ID lookup efficiency** — `_get_bot_id()` now checks the 3 deterministic `_id_paths()` first before falling back to the full 90-path brute-force search
- **ZW decode truncation** — `zwenc.decode()` now uses explicit `usable = len - len%4` truncation instead of the ambiguous `range(0, len-3, 4)`
- **Command deduplication** — `extract_commands()` now deduplicates commands found across marker-tag, code-block, and base64 formats
- **Temp file cleanup** — `pop_message()` schedules file deletion after 30 seconds via `threading.Timer`
- **Heartbeat write throttle** — heartbeat receiver now writes `bots.json` at most every 10 seconds instead of on every check-in
- **`dos` command signature** — removed unused `port` parameter; command now takes `dos <ip> <seconds>`
- **`--random` flag** — removed `default=True` which made the flag always active regardless of user intent
- **`total_hosts()` exclude overlap** — scope host count now subtracts addresses in exclude ranges and RFC-reserved blocks
- **`load_creds_file` safety** — returns empty list instead of crashing with `FileNotFoundError` when file is missing
- **HTTP cleartext warning** — API startup banner now warns that tokens are transmitted in cleartext without TLS

### Added
- **`genzw` command in server shell** — generates README with zero-width encoded payload (preferred format); `genzw pad` adds 1MB ZW padding
- **`rm_cmd` command in control shell** — remove individual commands by index instead of clearing all
- **`status` command in control shell** — quick summary showing repo count, command count, heartbeat and token status
- **Type annotations** — `PBConfig.repos`, `ControlConfig.repos`, `ControlConfig.commands`, `load_creds_file` return type now properly annotated
- **Python 3.10+ version check** — `oogascan.py` exits with clear error on older Python versions

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
