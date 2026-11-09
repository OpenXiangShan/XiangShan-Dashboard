# 内部性能看板：协作与维护

内部看板使用 `mainline-history-dashboard/v1` 数据，提供性能趋势、A/B 比较、
workload/切片分析、PERF 计数器、完整历史分析和 TEA+DIP 入口。
代码位于 `internal/`，构建产物为 `dist-internal/`。
仓库首页的 `scripts/update.py` 更新公开数据；本文的共享 SQLite 和内部看板
使用 `internal/data_import/tools/update_dashboard.py`。
默认公开构建 `npm run build` 和 Pages 工作流仍使用根目录的 `src/`、`data/`。

## 先确定要做什么

| 场景                      | 执行者         | 操作入口                                      |
| ------------------------- | -------------- | --------------------------------------------- |
| 修改代码、界面或导入逻辑  | 开发者         | 自己的 checkout → 个人分支 → PR               |
| 更新共享数据              | 有权限的协作者 | 更新主机上的 `update_dashboard.py --config …` |
| 自动更新、查看运行状态    | 部署维护者     | 维护账号的 systemd 用户任务及共享日志         |
| 将代码发布到现有网页      | 部署维护者     | 更新共享 `app/`，构建，按需重启服务           |
| 修正数据库、重新导出 JSON | 数据维护者     | 同一个更新锁内维护数据库并导出                |
| 更新分析报告、TEA+DIP     | 分析人员       | 分析工具 → 共享 `reports/`                    |

代码通过 Git 协作；数据通过共享目录协作。数据库、CI 证据、报告、缓存、
账号凭据和包含实际部署路径的配置都不提交到 Git。
内部部署主机、目录、团队组和维护账号以共享目录中的 `协作说明.md` 为准。

## 1. 加入协作

协作者需要能登录指定的更新主机，并属于共享目录的协作组；同时需要读取
CI 仿真报告、checkpoint profile 和 XiangShan Git 元数据的权限。
共享目录由维护者设置组读写权限、setgid 和新文件的组权限。
每个人使用自己的 GitHub 身份：推送代码需要仓库写权限或通过 fork 提交 PR，
更新数据需要自己的 `gh` 认证能够读取上游 CI。

本文命令使用以下变量。把示例路径改成自己的 **NFS 路径**；
`DASHBOARD_REPO` 是当前操作所使用的代码 checkout。

```bash
export DASHBOARD_DATA_DIR=/shared/xiangshan-dashboard
export DASHBOARD_REPO=/shared/my-work/XiangShan-Dashboard
export DASHBOARD_CONFIG="$DASHBOARD_DATA_DIR/config.json"
export TMPDIR="$DASHBOARD_DATA_DIR/tmp"
export SQLITE_TMPDIR="$TMPDIR"
umask 0007
```

在更新主机上检查自己的身份和认证：

```bash
id -nG
gh auth status
```

首次认证可运行 `gh auth login`。读取公共 CI 的认证和向 Dashboard 推送代码的
授权是两个独立条件；共享目录的文件权限也需要单独具备。

## 2. 修改代码并提交 PR

在自己的 NFS 工作目录检出内部功能分支，再创建个人分支：

```bash
git clone --branch feature/internal-performance-analysis \
  https://github.com/OpenXiangShan/XiangShan-Dashboard.git "$DASHBOARD_REPO"
cd "$DASHBOARD_REPO"
git switch -c improve-internal-dashboard
npm ci
```

Vue 界面在 `internal/src/`，完整历史界面在 `internal/legacy/`，
服务入口是 `internal/serve.py`，导入和分析工具在 `internal/data_import/`。
修改后按影响范围检查；涉及前端或构建配置时检查两种构建，
涉及导入逻辑时运行已有流水线测试：

```bash
npm run build
npm run build:internal
python3 -m unittest discover -s internal/data_import/tests -v
```

只暂存本次修改的文件，提交到个人分支，再推送：

```bash
git commit -m "Describe the change"
git push -u origin HEAD
```

向 `feature/internal-performance-analysis` 提交 PR；没有官方仓库写权限时，
推送到自己的 fork 后提交 PR。不要多人同时修改共享 `app/` 部署 checkout。
PR 合入后由维护者执行第 6 节的部署，代码推送本身不会更新线上页面。

本地预览复用可访问的共享 JSON，服务默认绑定 loopback：

```bash
python3 internal/serve.py --data-dir "$DASHBOARD_DATA_DIR" --port 8001
```

打开 `http://127.0.0.1:8001/xiangshan-performance-dashboard/`。
在另一台机器浏览时，可在自己的电脑建立转发：

```bash
ssh -N -L 8001:127.0.0.1:8001 your-user@dashboard-host
```

## 3. 手动更新共享数据

登录 `config.json` 中 `update_host` 指定的主机，使用部署代码和共享配置：

```bash
python3 "$DASHBOARD_DATA_DIR/app/internal/data_import/tools/update_dashboard.py" \
  --config "$DASHBOARD_CONFIG"
```

完整流程是：读取 GitHub Actions 元数据、job 日志和评分 artifact → 导入已有
仿真报告和 PERF 计数器到 SQLite → 恢复主线历史 → 导出看板 JSON。
工具不会从 GitHub 下载仿真报告；`report_root` 必须已经包含对应报告目录，
历史归档恢复还需要 `zstd`。需要 Python 3、Git、`gh` 和这些本地输入。

入口会检查更新主机，并持有 `performance.sqlite.update.lock` 直到整轮结束。
如果提示 `another dashboard update is running`，等待已有任务结束后再试；
不要删除锁文件。已导入的同一 CI 评分 artifact 会复用，不会重复创建测量。

| 需求                 | 在完整更新命令后附加             | 行为                                                         |
| -------------------- | -------------------------------- | ------------------------------------------------------------ |
| 只收集 CI 证据       | `--collect-only`                 | 收集 metadata、日志和评分 artifact，不导入 SQLite 或导出页面 |
| 使用已收集证据更新   | `--import-only`                  | 不访问 GitHub；使用现有证据和 Git 元数据执行导入及导出       |
| 扩大历史扫描范围     | `--page-limit 10 --max-runs 100` | 同时扩大扫描页数和本轮候选 run 数；默认分别为 3 和 20        |
| 调整计数器解析并行度 | `--jobs 4`                       | 调整并行解析任务数                                           |

`--import-only` 与 `--collect-only` 不能同时使用。常规更新保留完整流程；
`--skip-counters` 等跳过选项会影响恢复和导出范围，不适合作为默认更新方式。

看板读取 `mainline-history/` 中的 JSON，写入数据库后需要完成导出才会显示。
每次测量以 `run_id` 区分，同一提交可以有多次测量；0.3cov 和 1.0cov 保留各自身份。

## 4. 自动更新与排查

现有内部部署沿用以下用户服务名。服务属于**部署维护账号**；
协作者在自己的账号执行 `systemctl --user`，看到的是自己的用户任务。
查看或管理这些任务时先登录对应的维护账号。

```bash
systemctl --user status commit-ipc-trend-dashboard.service
systemctl --user list-timers --all commit-ipc-trend-update.timer
systemctl --user show commit-ipc-trend-update.service \
  -p ActiveState -p Result -p ExecMainStatus -p ExecMainExitTimestamp
tail -n 80 "$DASHBOARD_DATA_DIR/update.log"
tail -n 80 "$DASHBOARD_DATA_DIR/web.log"
```

当前定时器按 15 分钟间隔触发；一轮可能超过 15 分钟，已在运行的服务不会再启动
第二份。更新服务的 `activating/start` 表示整轮仍在运行；完成后
`inactive/dead` 且退出码为 0 是正常状态。完成前显示的 `Result` 不能单独证明
当前这一轮已成功，需结合日志、退出时间及 JSON 更新时间判断。

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path
manifest = Path(os.environ["DASHBOARD_DATA_DIR"]) / "mainline-history/manifest.json"
data = json.loads(manifest.read_text())
print("生成时间（UTC）:", data["generated_at"])
print("测量数:", data["run_count"])
PY
```

| 现象                                | 检查与处理                                                        |
| ----------------------------------- | ----------------------------------------------------------------- |
| 更新锁已占用 / `database is locked` | 查看已有任务进度，等待其结束；不要并发启动低层导入脚本            |
| `run this update on …`              | 登录配置中的更新主机；同名 NFS 路径不一定属于同一存储后端         |
| GitHub 认证失败                     | 在实际运行账号下检查 `gh auth status` 和上游仓库访问权限          |
| `missing_report`                    | 检查 `report_root` 下的报告；只有 CI 元数据不足以导入             |
| CI 运行未成功或缺少评分 artifact    | 本轮会跳过，查看 `weekly-spec06-gcc16/update-summary.json` 的原因 |
| 导入后页面没有新数据                | 确认导出已完成、manifest 更新时间、所选数据集和覆盖范围           |
| 没有文件权限                        | 由维护者核对协作组、目录继承组和组读写权限                        |

## 5. 新建一套共享配置

已有部署直接复用其配置；只有新建独立部署时才复制示例：

```bash
cp "$DASHBOARD_REPO/internal/data_import/config.example.json" "$DASHBOARD_CONFIG"
```

填写 `data_dir`、`update_host`、`report_root`、`git_repo` 和 `gcc16_profile`。
`git_repo` 指向 XiangShan 的 checkout 或共享 bare 元数据仓库，
不是本 Dashboard 仓库。相对路径按配置文件所在目录解析，显式 CLI 参数优先。
JSON 仅接受示例中的路径字段、`update_host`，以及可选的
`db`、`evidence`、`output`、`dashboard_output` 路径覆盖；并行度等参数通过 CLI 传入。

| 共享目录内容           | 用途                                 |
| ---------------------- | ------------------------------------ |
| `app/`                 | Dashboard 部署 checkout              |
| `config.json`          | 当前环境配置                         |
| `performance.sqlite`   | 生产数据库                           |
| `evidence/`            | 收集的 CI 证据                       |
| `weekly-spec06-gcc16/` | 收据、manifest、计数器缓存和更新摘要 |
| `spec06-gcc-recovery/` | 主线历史恢复文件及缓存               |
| `mainline-history/`    | 网页读取的 JSON                      |
| `reports/`             | 独立生成的分析报告和 TEA+DIP 页面    |
| `tmp/`                 | 共享临时目录                         |

维护者负责创建目录及配置协作组权限。所有数据库、备份、临时文件和缓存都放在
共享 NFS。入口默认会写到仓库的 `internal/data_import/local/`，该目录已被 Git
忽略；协作部署必须传入共享配置，避免意外使用这个默认目录。
服务可通过 `--data-dir` 读取默认布局，或通过 `--data-root` 指定实际 JSON 目录；
报告目录位于该 JSON 目录的同级 `reports/`。

## 6. 将合入代码部署到现有网页

以下由维护者执行。先暂停后续定时触发，等待当前更新服务结束；
停止 timer 不会停止已经开始的导入：

```bash
systemctl --user stop commit-ipc-trend-update.timer
systemctl --user show commit-ipc-trend-update.service -p ActiveState
```

确认更新服务为 `inactive`，并确认部署 checkout 没有待处理修改后执行：

```bash
cd "$DASHBOARD_DATA_DIR/app"
umask 0007
git pull --ff-only origin feature/internal-performance-analysis
npm ci
DASHBOARD_BUILD_DIR=$(mktemp -d "$DASHBOARD_DATA_DIR/tmp/internal-build.XXXXXX")
chmod 2770 "$DASHBOARD_BUILD_DIR"
npm run build:internal -- --outDir "$DASHBOARD_BUILD_DIR"
```

只有拉取和构建成功后，才替换正在提供服务的构建并重启：

```bash
mv dist-internal "$DASHBOARD_DATA_DIR/tmp/dist-internal-$(date +%Y%m%d-%H%M%S)"
mv "$DASHBOARD_BUILD_DIR" dist-internal
systemctl --user restart commit-ipc-trend-dashboard.service
systemctl --user start commit-ipc-trend-update.timer
```

临时构建和旧构建都在共享 NFS。若构建失败，不执行替换步骤；修复失败的部署
或恢复原代码版本后，恢复数据定时器。服务读取部署目录的 `dist-internal/`。
确认主看板、完整历史、分析报告、TEA+DIP 入口可访问，并检查定时器已恢复。

## 7. 修正数据库，或只重新导出 JSON

优先修正导入/计算逻辑，使用可复现的输入重建结果。同一 CI artifact 已导入后会
复用，编辑证据文件再运行 `--import-only` 不保证覆盖它。手工修改派生测量也可能
在下一次恢复流程中被重算，因此持久修复应落实到产生该结果的代码或输入。

必须直接修正数据库时，由维护者暂停 timer、等待当前任务结束，在共享 NFS
保存 SQLite 备份，并让修改脚本使用 `tools.update_dashboard.update_lock`。
在锁内使用 SQLite 事务，只修改目标行，记录 run ID 和修改依据，随后导出。
不要绕开更新锁执行会写数据库的低层脚本。

只需要把当前已发布的测量重新导出时，可以在更新主机执行下面的命令。
它复用同一个锁，并保留现有看板的测量选择：

```bash
PYTHONPATH="$DASHBOARD_DATA_DIR/app/internal/data_import" python3 - <<'PY'
import json
import os
from commit_ipc_trend.dashboard_export import export_history
from tools.update_dashboard import parse_args, update_lock

args = parse_args(["--config", os.environ["DASHBOARD_CONFIG"]])
os.umask(0o007)
os.environ["SQLITE_TMPDIR"] = str(args.data_dir / "tmp")
with update_lock(args.db):
    catalog = json.loads((args.dashboard_output / "manifest.json").read_text())
    dataset = next(row for row in catalog["datasets"] if row["profile"] == "all-measurements")
    payload = json.loads((args.dashboard_output / f"{dataset['id']}.json").read_text())
    export_history(args.db, args.dashboard_output, runs=[row["run_id"] for row in payload["runs"]])
PY
```

单独调用 `export_dashboard_history.py` 时，不带 `--run` 会导出全库，范围可能大于
当前线上看板；需要明确选取时重复传入 `--run`。修复或导出完成后恢复 timer。

## 8. 更新报告与 TEA+DIP

自动导入只更新 SQLite 和看板 JSON。分析报告和 TEA+DIP 是独立生成的快照，
不会随 timer 自动重算；页面应保留各自数据时间与来源。
已有报告分别从 `reports/20261103/` 和 `reports/tea-dip/` 提供。

A/B 诊断、滚动分析、切片敏感度、异常导出等入口都在
`internal/data_import/tools/`，通过各工具的 `--help` 查看所需输入。
分析输出放在自己的共享目录，确认结果后由维护者发布到 `reports/`。
例如，已有 `raw/`、`analysis/` 的 RTL 证据可以生成 TEA+DIP 页面：

```bash
python3 "$DASHBOARD_DATA_DIR/app/internal/data_import/tools/link_tea_dip.py" \
  --db "$DASHBOARD_DATA_DIR/performance.sqlite" \
  --evidence-dir /shared/rtl-evidence \
  --output "$DASHBOARD_DATA_DIR/reports/tea-dip-preview"
```

预览位于 `/xiangshan-performance-dashboard/reports/tea-dip-preview/`。
审核后发布到 `reports/tea-dip/` 才会更新现有菜单入口。
此工具生成的 Markdown、JSON、CSV 和 HTML 都是报告产物，保留在共享目录。

绘图工具需要 `internal/data_import/requirements.txt` 中的可选依赖。
当前部署可使用共享 `analysis-venv/bin/python`，并把 `MPLCONFIGDIR` 指向
共享 `cache/matplotlib`；日常导入不需要安装绘图依赖。
