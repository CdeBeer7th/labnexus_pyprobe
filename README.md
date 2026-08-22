# pyProbe

Automatic experimental-data sync for your [LabNexus](https://github.com/CdeBeer7th/labnexus_server) server.

Point pyProbe at a folder, give it your server, and it uploads new files as they appear —
in a live terminal dashboard, a desktop window, or silently in the background.

```
╭─ pyProbe ───────────────────────────────────────────────────────────────╮
│   server  http://lab.example.com:8000                                   │
│ watching  /data/instrument-1  (top level)                               │
│ matching  *.csv *.txt  - 8 exclusion(s)                                 │
│  session  me@lab.org  connected                                         │
╰─────────────────────────────────────────────────────────────────────────╯
 time      file                          size  status  detail
 10:04:12  run_014.csv                  2.1 MB  OK
 10:04:17  run_015.csv                  1.8 MB  OK
 10:04:22  notes.txt                    4.0 KB  FAIL    HTTP 413 - too large
─────────────────────────────────────────────────────────────────────────────
⠙ watching          2 uploaded - 1 failed - 3.9 MB - up 0:14 - next scan 3s
```

## Install

pyProbe uses [uv](https://docs.astral.sh/uv/) for Python and dependency management.
[Install uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
# run it without installing anything permanently
uvx --from git+https://github.com/CdeBeer7th/labnexus_pyprobe labnexus-pyprobe --help

# or install it as a tool on your PATH
uv tool install git+https://github.com/CdeBeer7th/labnexus_pyprobe
```

uv fetches a matching Python itself, so you don't need one installed first.
On Windows, add the `notify` extra for native toast notifications:

```bash
uv tool install "labnexus-pyprobe[notify] @ git+https://github.com/CdeBeer7th/labnexus_pyprobe"
```

## Use

```bash
labnexus-pyprobe ~/data lab.example.com:8000
```

pyProbe asks for your LabNexus email and password, opens a session, and starts watching.
Press `ctrl-c` to stop. Files already uploaded are remembered across restarts.

A few common variations:

```bash
# only CSV and TXT, including subfolders, scanned every 30 seconds
labnexus-pyprobe -d ~/data -s lab.example.com -p '*.csv' -p '*.txt' -r -i 30

# desktop window instead of the terminal
labnexus-pyprobe --gui

# see what would be uploaded, send nothing
labnexus-pyprobe ~/data lab.example.com --dry-run

# unattended: no prompts, plain log output to a file
LABNEXUS_PASSWORD=... labnexus-pyprobe ~/data lab.example.com \
    -e me@lab.org --plain --log-file ~/pyprobe.log

# check the server and your credentials, then exit
labnexus-pyprobe ~/data lab.example.com --check
```

Run `labnexus-pyprobe --help` for the full list of options.

### Interfaces

| Flag | What you get |
| --- | --- |
| *(default)* | Live terminal dashboard when stdout is a terminal, plain logs otherwise |
| `--gui` | Desktop window: fill in the form, press Start, watch the log |
| `--plain` | Timestamped log lines — for `nohup`, systemd, Task Scheduler, cron |

Desktop notifications are on by default and use whatever the OS provides
(`notify-send` on Linux, `osascript` on macOS, toasts on Windows). `--no-notify` turns them off.

### Choosing what gets uploaded

- `-p/--pattern` — glob(s) to include; repeat the flag for several. Default: everything.
- `-x/--exclude` — glob(s) to skip, on top of the built-in list (dotfiles, `*.tmp`,
  `*.part`, Office lock files, …). `--no-default-excludes` drops the built-ins.
- `-r/--recursive` — also watch subdirectories.
- `--only-new` — ignore whatever is already in the folder when the session starts.
- `--min-age SEC` — wait this long after the last write before uploading, so a file an
  instrument is still writing isn't sent half-finished. Default 2s.

pyProbe hashes each file, so a file that is *overwritten* with new contents is uploaded
again. Pass `--no-reupload-changed` if you only ever want each filename sent once.

### Environment variables

| Variable | Equivalent |
| --- | --- |
| `LABNEXUS_SERVER` | `--server` |
| `LABNEXUS_DIR` | `--directory` |
| `LABNEXUS_EMAIL` | `--email` |
| `LABNEXUS_PASSWORD` | password, instead of prompting |
| `LABNEXUS_TOKEN` | an existing access token — skips login entirely |

## Development

```bash
git clone https://github.com/CdeBeer7th/labnexus_pyprobe
cd labnexus_pyprobe
uv sync --extra notify   # creates .venv from uv.lock
uv run pytest            # test suite (spins up a stand-in LabNexus server)
uv run ruff check .      # lint
uv run labnexus-pyprobe --help
```

`.python-version` pins the interpreter and `uv.lock` pins every dependency, so
`uv sync` gives everyone the same environment. To change a dependency, edit
`pyproject.toml` and run `uv lock`; to release, bump `version` in `pyproject.toml`
(or `uv version --bump patch`) and tag.

### Layout

| Module | Responsibility |
| --- | --- |
| `cli.py` | Argument parsing, environment defaults, front-end selection |
| `config.py` | `Settings`: what to watch, include/exclude matching |
| `client.py` | LabNexus HTTP: login, token, uploads with retries |
| `watcher.py` | The scan loop, change detection, upload history — emits `ProbeEvent`s |
| `tui.py` | Rich terminal dashboard |
| `gui.py` | Tkinter desktop window |
| `plain.py` | Plain logging front end |
| `notify.py` | Cross-platform desktop notifications |
| `prober.py` | Backwards-compatible `FileWatcher()` wrapper |

Every front end consumes the same `ProbeEvent` stream from `watcher.py`, so behaviour
can't drift between them.
