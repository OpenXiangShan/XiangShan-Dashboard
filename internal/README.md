# Optional internal performance analysis

This page uses the private `mainline-history-dashboard/v1` export from
`commit_ipc_trend`. It is separate from the public XiangShan Dashboard app.
The official `npm run build` and Pages workflow continue to build only the
root `src/` and public `data/`; no private export belongs in this repository.

```bash
npm ci
npm run build:internal
python3 internal/serve.py \
  --data-root /path/to/commit_ipc_trend/outputs/mainline-history \
  --legacy-root /path/to/commit_ipc_trend/xiangshan-performance-dashboard
```

Open `http://127.0.0.1:8001/` (or use an SSH tunnel). The server binds to
loopback by default. The optional `--legacy-root` enables the existing
correlation page and generated report links. The internal build writes to
`dist-internal/`, which is Git-ignored and is not copied into public `dist/`.

The app retains distinct run IDs, score coverage, common-slice IPC comparison,
PERF-window counter checks, TEA+DIP links, and CSV/PNG exports. The frontend
shares only code, not measurement data, with the public repository.
