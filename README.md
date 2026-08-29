# pyProbe

Automatic experimental-data sync for your [LabNexus](https://github.com/CdeBeer7th/labnexus_server) server.

Point pyProbe at a folder, give it your server, and it uploads new files as they appear —
in a live terminal dashboard, a desktop window, or silently in the background.

Give a folder a **spectrometer** and it does more: the plate-reader export is parsed at
the bench into a structured, validated document and uploaded as data, not just as a file.
One folder per instrument, as many as you have readers.

```
╭─ pyProbe ───────────────────────────────────────────────────────────────╮
│    server  http://lab.example.com:8000                                  │
│ workspace  11111111-2222-3333-4444-555555555555                         │
│  watching  /data/spark  (top level)                                     │
│              Tecan Spark  *.xlsx *.xls                                  │
│            /data/epoch  (top level)                                     │
│              Agilent Biotek Epoch 2  *.xlsx *.xls                       │
│   session  me@lab.org  connected                                        │
╰─────────────────────────────────────────────────────────────────────────╯
 time      file                          size  status  detail
 10:04:12  NAD_run1.xlsx                40.7 KB  OK     1 group, 45 series
 10:04:17  plate_A.xlsx                 70.8 KB  OK     1 group, 36 series
 10:04:22  draft.xlsx                    4.0 KB  FAIL   Tecan Spark: no data header
─────────────────────────────────────────────────────────────────────────────
⠙ watching          2 uploaded - 1 failed - 111 KB - up 0:14 - next scan 3s
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

Run it with no arguments and the desktop window opens, where you pick the folder to
watch and type the server address:

```bash
labnexus-pyprobe
```

Or give it both up front and stay in the terminal:

```bash
labnexus-pyprobe ~/data lab.example.com:8000
```

pyProbe asks for your LabNexus email and password, opens a session, and starts watching.
Press `ctrl-c` to stop. Files already uploaded are remembered across restarts.

For a bench nobody is sitting at, use a **pyProbe access token** instead of a password —
see [Signing in unattended](#signing-in-unattended) below.

A few common variations:

```bash
# only CSV and TXT, including subfolders, scanned every 30 seconds
labnexus-pyprobe -d ~/data -s lab.example.com -p '*.csv' -p '*.txt' -r -i 30

# desktop window with the form already filled in
labnexus-pyprobe --gui -d ~/data -s lab.example.com:8000

# see what would be uploaded, send nothing
labnexus-pyprobe ~/data lab.example.com --dry-run

# unattended: a pyProbe token, no prompts, plain log output to a file
LABNEXUS_PYPROBE_TOKEN=lnxp_... labnexus-pyprobe ~/data lab.example.com \
    -e me@lab.org --plain --log-file ~/pyprobe.log

# check the server and your credentials, then exit
labnexus-pyprobe ~/data lab.example.com --check
```

### Signing in unattended

A password login is the wrong shape for an instrument PC: the server may ask for a
CAPTCHA or a second factor, and neither can be answered by a process running on its own
overnight. A **pyProbe access token** is the credential built for that case.

Mint one in the LabNexus web interface under **Settings → pyProbe**. Give it a name you
will recognise (`Bench 3 — SpectraMax`) and an expiry, and copy the `lnxp_…` value — it
is shown once and stored only as a hash, so a lost token is replaced rather than
recovered. Then hand it to pyProbe together with the account email:

```bash
labnexus-pyprobe ~/readers/spark lab.example.com \
    -e me@lab.org --pyprobe-token lnxp_... -m tecan-spark --plain

# or, better for a service or a cron job — keep it out of the process list
LABNEXUS_EMAIL=me@lab.org LABNEXUS_PYPROBE_TOKEN=lnxp_... \
    labnexus-pyprobe ~/readers/spark lab.example.com -m tecan-spark --plain
```

Both halves are required: the token alone will not open a session, so a copy of it
lifted from a config file is not usable without knowing whose it is.

What this buys you over `--password-stdin` / `LABNEXUS_PASSWORD`:

- **No CAPTCHA, no second factor.** The session pyProbe gets is marked as a bench
  session, so the server does not re-challenge it on an IP change or an upload burst.
- **It expires on a date you chose**, and the session it buys never outlives the token.
- **It is revocable on its own.** Delete it from Settings → pyProbe and that bench stops
  syncing; your password and every other install are untouched.
- **It is not your password.** A token cannot change your email, your password, or
  anything else about the account.

`--token` is still there for a session JWT you obtained yourself; it skips the exchange
entirely.

### Spectrometers

A **queue** is a folder plus the instrument that writes into it. Give each reader its own
drop folder and pyProbe parses each one as the right vendor format:

```bash
labnexus-pyprobe -s lab.example.com -w WORKSPACE_ID \
    -Q ~/readers/spark=tecan-spark \
    -Q ~/readers/epoch=biotek-epoch-2 \
    -Q ~/readers/spectramax=spectramax-190
```

With one instrument, `--spectrometer` is shorter:

```bash
labnexus-pyprobe ~/readers/spark lab.example.com -m tecan-spark -w WORKSPACE_ID
```

What this changes:

- **The upload is structured data.** Each export is parsed into a validated
  `UnifiedPlateReaderOutput` — instrument and software metadata, per-well series, shared
  time/wavelength axes — and sent alongside the original file. The server re-validates it
  and files both.
- **A queue only picks up its instrument's exports.** A Tecan Spark folder matches
  `*.xlsx`/`*.xls` and a SpectraMax folder matches `*.txt`, without you saying so. An
  explicit `-p/--pattern` still overrides it.
- **A file that won't parse is never uploaded.** It's reported and retried on the next
  scan, so a half-written export sorts itself out once the instrument finishes.
- **`--dry-run` still parses**, so you can see exactly what a folder produces before
  sending anything.

Parsing happens at the bench using
[labnexus-plate-parsers](https://github.com/CdeBeer7th/labnexus_plate_parsers), the same
package the server runs — so the structured output is identical either way.

```bash
labnexus-pyprobe --list-models        # supported instruments and their file types
labnexus-pyprobe --list-workspaces -s lab.example.com -e me@lab.org
```

| Instrument | `--queue PATH=MODEL` | Exports |
| --- | --- | --- |
| ThermoFischer Multiskan SkyHigh | `multiskan-skyhigh` | `*.xlsx` `*.xls` |
| BMG Labtech SPECTROStar Nano | `spectrostar-nano` | `*.xlsx` `*.xls` |
| Tecan Spark | `tecan-spark` | `*.xlsx` `*.xls` |
| Tecan Spark (SparkControl) | `tecan-spark-control` | `*.xlsx` `*.xls` |
| Tecan Magellan | `tecan-magellan` | `*.xlsx` `*.xls` |
| Agilent Biotek Epoch 2 | `biotek-epoch-2` | `*.xlsx` `*.xls` |
| Molecular Devices SpectraMax 190 | `spectramax-190` | `*.txt` |
| ThermoFischer Multiskan Spectrum 1500 | `multiskan-spectrum-1500` | `*.txt` |

Spelling is forgiving — `tecan-spark`, `tecanSpark`, `TECAN SPARK` and `Tecan Spark` all
resolve to the same instrument.

`--workspace` is optional. Left out, the server files parsed runs under this account's
**Pyprobe** workspace, creating it the first time one arrives — which is what you want on
a bench where nobody is choosing a destination. Pass `--workspace` to send a particular
reader's output somewhere specific; `--list-workspaces` prints the ids you can use.
Folders given without an instrument are uploaded as plain files, as before.

Run `labnexus-pyprobe --help` for the full list of options.

### Interfaces

| Flag | What you get |
| --- | --- |
| *(no arguments)* | Desktop window, so there is somewhere to enter the folder and server |
| *(any argument)* | Live terminal dashboard when stdout is a terminal, plain logs otherwise |
| `--gui` | Desktop window: fill in the form, press Start, watch the log |
| `--plain` | Timestamped log lines — for `nohup`, systemd, Task Scheduler, cron |

`--gui` accepts the same options as the terminal front ends — `-d`, `-s`, `-p`, `-r`,
`-i`, `--scheme` and so on prefill the form, and anything you leave out you fill in
in the window. Where no window can be opened (a headless Linux box with no `DISPLAY`),
the bare command falls back to the terminal front end.

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
| `LABNEXUS_PYPROBE_TOKEN` | `--pyprobe-token` — the unattended credential |
| `LABNEXUS_TOKEN` | an existing session JWT — skips login entirely |
| `LABNEXUS_WORKSPACE` | `--workspace` |
| `LABNEXUS_SPECTROMETER` | `--spectrometer` |

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
| `config.py` | `Queue` (a folder + its instrument) and `Settings`: what to watch |
| `client.py` | LabNexus HTTP: password and pyProbe-token sign-in, workspaces, plain and structured uploads |
| `watcher.py` | The scan loop, parsing, change detection, history — emits `ProbeEvent`s |
| `tui.py` | Rich terminal dashboard |
| `gui.py` | Tkinter desktop window |
| `plain.py` | Plain logging front end |
| `notify.py` | Cross-platform desktop notifications |
| `prober.py` | Backwards-compatible `FileWatcher()` wrapper |

Every front end consumes the same `ProbeEvent` stream from `watcher.py`, so behaviour
can't drift between them.

Parsers are **not** in this repo. They live in
[labnexus-plate-parsers](https://github.com/CdeBeer7th/labnexus_plate_parsers), which both
pyProbe and the LabNexus server depend on, so the two cannot disagree about what a file
means. It is vendored here as the **git submodule** `vendor/labnexus_plate_parsers`, at
the same path and pinned to the same commit as in the server repo — the submodule pointer
is what keeps bench-side and server-side parsing identical.

```bash
git clone --recurse-submodules https://github.com/CdeBeer7th/labnexus_pyprobe.git
git submodule update --init vendor/labnexus_plate_parsers   # existing clone
git submodule update --remote vendor/labnexus_plate_parsers # pull latest main
```

`uv sync` builds it straight from that directory, so edits to a parser are picked up by
the next `uv sync` with no reinstall dance. Publishing a parser change is two commits here
(one inside `vendor/labnexus_plate_parsers`, pushed; one recording the new pointer plus
`uv lock`) and the matching pointer bump in the server repo.

`uv sync` therefore **requires the submodule** — without it uv reports
`Distribution not found at: file:///.../vendor/labnexus_plate_parsers`.
