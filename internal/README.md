# Optional internal performance analysis

This page uses the private `mainline-history-dashboard/v1` export. It is
separate from the public XiangShan Dashboard app. The official `npm run build`
and Pages workflow continue to build only the root `src/` and public `data/`.

```bash
npm ci
npm run build:internal
python3 internal/serve.py \
  --data-root internal/data_import/local/mainline-history
```

Open `http://127.0.0.1:8001/` (or use an SSH tunnel). The server binds to
loopback by default. The optional `--legacy-root` enables the existing
correlation page and generated report links. The internal build writes to
`dist-internal/`, which is Git-ignored and is not copied into public `dist/`.

The app retains distinct run IDs, score coverage, common-slice IPC comparison,
PERF-window counter checks, TEA+DIP links, and CSV/PNG exports.

## Local Data Import

The importer in `internal/data_import/` collects authenticated GitHub Actions
metadata and score artifacts, imports local performance reports and PERF
counters into SQLite, recovers mainline history, then exports the JSON used by
this page. It does not download simulator reports; `--report-root` must point
to a local report tree that contains them. The XiangShan checkout and GCC16
profile are also local inputs.

Install `gh` and `zstd`, and authenticate `gh` for
`OpenXiangShan/XiangShan`. Run the complete update from a host that can access
GitHub and the XiangShan Git remote:

```bash
python3 internal/data_import/tools/update_dashboard.py \
  --report-root /path/to/perf-report \
  --git-repo /path/to/XiangShan \
  --gcc16-profile /path/to/checkpoints_all.json
```

SQLite, collected CI evidence, intermediate manifests, recovery caches, and
dashboard JSON are written under `internal/data_import/local/`. That directory
is Git-ignored. The default JSON output is
`internal/data_import/local/mainline-history`; pass it to `internal/serve.py`
as shown above. `--collect-only` refreshes CI evidence without importing it;
`--import-only` rebuilds SQLite and dashboard JSON from collected evidence
without contacting GitHub.

## Shared deployment

Keep a single data directory on storage accessible to the team. It contains
`performance.sqlite`, `evidence/`, `weekly-spec06-gcc16/`, `mainline-history/`,
and `tmp/`. For NFS storage, run the updater on one designated host. Other
collaborators log in to that host to start an update; the page only reads the
exported JSON.

Copy `internal/data_import/config.example.json` to the shared data directory
as `config.json` and fill in the host and input paths. Relative paths in the
configuration are resolved against the configuration file's directory. Keep
this deployment configuration and measurement data outside Git.

```bash
python3 internal/data_import/tools/update_dashboard.py \
  --config /shared/xiangshan-dashboard/config.json

python3 internal/serve.py --data-dir /shared/xiangshan-dashboard
```

The updater checks `update_host` and locks the database for the entire update,
including evidence collection and JSON export. A second update exits with a
message instead of running concurrently. The lock is released on exit; keep
the `.update.lock` file in place. Use the same shared configuration for manual
and scheduled updates.
Previously imported CI score artifacts are reused, so relocating the data
directory does not create another measurement for the same evidence.

Give the collaborator group write and search access to the data directory,
use setgid directories so new files inherit that group, and give that group
read/write access to the database and existing evidence/cache files. The
updater creates new files with group access and stores SQLite temporary files
in the shared `tmp/` directory. Each collaborator also needs access to the CI
reports, profile and XiangShan checkout, plus an authenticated `gh` session on
the update host.
