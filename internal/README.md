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
