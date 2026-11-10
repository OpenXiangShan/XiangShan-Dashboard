# GEM5 开发者性能分析（内网第一版）

在现有 Performance Compare 中增加按需详细分析：左侧选择已有 RTL / GEM5
baseline，另一侧输入手动 CI 的 URL 或 Run ID。点击“详细分析”后，服务定位
NFS 归档，调用现有 `gem5_data_proc` 提取、加权、算分，再通过同一套查询接口展示结果。

## 本地启动

需要 Node.js、Python 3.12，以及可用的 gem5_data_proc 环境（pandas、numpy、scipy、PyYAML）。
服务所在机器需要能够读取 NFS；GEM5 归档的查询不依赖 GitHub token 或下载 Actions 日志；RTL 来源首次定位需要可用的 `gh api` 登录和 Actions 日志。

处理器需要包含 `analysis_pipeline.py` 与 `analysis_inputs.py`，旧 checkout 会在启动时明确报错。
组内试用的处理器分支为 `feature/raw-simpoint-query`（基础入口已合入主线 `graphmaker`），接口见处理器的 `docs/analysis_pipeline.md`。

```bash
npm ci
npm run build:internal
python internal/serve_gem5.py \
  --processor /nfs/home/yanyue/codex-tasks/gem5-performance-platform/gem5_data_proc \
  --archive-root /nfs/home/share/gem5_ci/performance_data \
  --port 8002
```

浏览器打开 `http://127.0.0.1:8002/?analysis=1`。
支持通过 `&a=<Run ID>&b=<Run ID>` 预填输入框，仍需点击按钮开始分析。

如果服务运行在 node037，在自己的电脑建立 SSH 转发：

```bash
ssh -N -L 8002:127.0.0.1:8002 node037
```

然后访问自己电脑上的上述地址。服务默认仅监听 loopback。后续部署到团队内网时，
应放在已有内网访问控制之后；本版没有增加账号系统。

`npm run build` 仍构建原有公开页面到 `dist/`；详细分析仅进入
`npm run build:internal` 的 `dist-internal/`。公开构建不会包含详细分析组件或 API 调用代码。
组内服务与公开 Pages 发布入口独立；缓存和运行日志不提交到仓库。

## 使用方式

1. 在性能对比左侧选择 RTL / GEM5 数据集与 baseline，A 输入框留空即可引用它。
2. B 输入框粘贴 `https://github.com/OpenXiangShan/GEM5/actions/runs/<ID>`，或 `https://github.com/OpenXiangShan/XiangShan/actions/runs/<ID>`，也可直接填数字。
   显式链接决定来源；引用页面 baseline 时继承数据集的 RTL / GEM5 类型。
3. 点击“详细分析”。同一个 Run 存在多个配置/数据集时，确认归档选择后再次点击。
   处理中显示阶段、切片进度和累计耗时；点击“停止分析”可取消运行或排队中的任务。
   刷新或关闭页面本身不会取消后台任务；同一标签页刷新后会恢复任务连接及停止按钮。
4. 首次处理后可搜索 YAML 别名或 stats 原始名称。新增计数器无需预先写入 YAML。
5. 选择指标、Group、Benchmark、SimPoint、排序及变化阈值，点击“应用筛选并查询”。
   选中一个 Group 即加入全组，取消即移除该组独有项。
   默认按绝对差值 `|B − A|` 降序，正负变化都按大小排列；缺失差值排末尾，可切回百分比排序。
6. 详细分析默认查询 CPI 和 Intel Topdown L1 四项，并在筛选区后、计数器明细前展示 L1 的 A/B 堆叠百分比图。
   可切换相对变化概览或 A/B 单指标柱状图；选择 intel_topdown 分类仍会加入全组指标。
   图旁逐侧列出缺失分量及数量，空白不表示零；负值、超过 100% 和四项不闭合会明确提示，保留原值。
   当前 RTL 缺少旧公式依赖的 inst_spec / recovery_bubble，Bad Speculation 和 Backend Bound 无法还原。
   Rename 的 recovery_stall 只统计 RAB walk 周期，不能代替 recovery bubble 槽位。
   Topdown 图是逐切片比率的加权均值，不等同于加权计数之比，也不直接代表节省周期。
   CSV 导出全部筛选结果，表格按每页 100 行展示。

查询单个 raw 值时，选择 `SimPoint` 层级和具体切片：后端先筛选切片，只读取所选 raw 缓存，
直接返回最终完整测量窗口的值，不加权。Benchmark 层级的 raw 查询仍按已有口径加权。

原始计数器使用 `raw:<完整 stats 名称>` 标识，例如
`raw:system.cpu.commit.branchMispredicts`。YAML 指标直接使用其别名，如 `cpi`。
Group 来自当前处理器 `targets/*.yaml` 和 `targets/local`，不维护第二份计数器字典。
原始计数器在“全部”分组中搜索。每次最多查询 512 个指标。点击分类会选入全组指标，取消分类会移除该组独有指标，
保留仍属于其他已选分类的共享项；已手动选中的其他指标也会保留。分类选择不受搜索和分页影响。

## 处理和比较口径

- 使用 `metadata.txt` 中的 `workflow_run_id` 建立轻量 NFS 索引，不通过时间戳猜测目录。
- 使用归档的实际 `commit`、`cluster.json`、配置与参数；不以 GitHub workflow head SHA 代替执行源码。
- GEM5 只处理带 `completed`、无 `abort`、具有 warmup 和完整最终 stats 窗口的切片。
  RTL 使用实际执行 job 的 `SPEC_DIR`、`CKPT_JSON_PATH`、`SCORE_FILE` 和 checkout SHA；
  不从日期/commit 猜路径，不把脚本中的示例 env 当成执行证据。按 profile 的 workload/point/weight
  选择目录，要求成功结束标记与单核 warmup/final PERF dump。两者的必需 cycles、committedInsts、CPI 必须为正的有限值。
- RTL 同一 commit 报告目录可能被多次 CI 复用，展示的是当前现存报告，不能承诺还原历史 Run。
  跨 RTL/GEM5 同名 YAML 指标需要核对口径；单侧缺失为 null，不自动映射 raw 名称。
  RTL raw 名称为 `raw:<完整层次>:<counter>`。
- 初次处理由 `batch.py -g all` 提取 YAML 指标；Benchmark 加权和 SPEC 算分由
  `simpoint_cpt.compute_weighted` 完成。任意原始计数器也交给该加权器处理。
- 加权 IPC 定义为 `1 / weighted_CPI`。其他指标遵循现有处理器语义；
  原始比率的加权平均不等同于加权计数的比率，不自动推断所有统计项的归约规则。
- 缺失/NaN/Inf 返回 JSON `null`，不补零；A 为零时，非零变化不计算百分比。
- 顶部展示原始运行 score/GHz；Benchmark/SimPoint 筛选只改变查询明细，不重新定义顶部套件分数。
- 分别展示完成切片数和 profile 权重覆盖率。部分运行可诊断，但会标出警告；
  不同有效切片集合的加权结果不可视为严格匹配的 A/B。
- profile 不同时不计算逐切片差值。配置和参数差异会明确展示，供开发者判断。
- 原始计数器变化只表示 B 相对 A 的增减，不自动判断收益或归因。

## 数据服务和并发

只有三层：Vue 页面 → `AnalysisService` → 独立 gem5_data_proc 子进程。
Dashboard 保留 Run/NFS 定位、队列与取消、缓存身份/发布、查询筛选和展示。
日志解析、切片选择、原始缓存生成、YAML 提取、加权和算分统一由处理器提供。
`internal/analysis_worker.py` 只 exec 处理器的 `analysis_pipeline.py`；
RTL 的 GitHub receipt 与路径白名单仍在 Dashboard 的 `rtl_sources.py`。
缓存签名从处理器 `analysis_inputs.py` 复用 RTL 切片选择，不在 Dashboard 重写解析规则。
`gem5_data_proc/batch.py --jobs` 本身已经支持并行，本分支继续直接调用它。
新增的并行只用于之前串行的原始计数器索引/缓存阶段。
子进程隔离现有处理器的全局状态；NFS 原始数据只读。

默认一个分析 worker，收集原始计数器时最多 4 个线程，随后 YAML 提取最多 4 个进程，
两个阶段依次执行；最多 8 个运行中/排队任务。NumPy/BLAS 内部线程固定为 1，防止隐式扩张。
可通过 `--workers`、`--extract-jobs`、`--max-pending` 调整。同一进行中查询会合并；
取消或超时后终止其进程组，并清理本次未完成的快照和临时数据。第一版使用单服务进程，暂不支持多个服务共同写同一缓存。

缓存位于 Git 忽略的 `internal/local/gem5/`，保存压缩原始计数器、CSV、来源和任务日志。
缓存身份包含 stats 文件大小/修改时间、完成状态、profile、metadata 和处理器源码/YAML 内容。
处理结束再次检查输入身份，发生变化则拒绝发布该快照。服务重启后可复用处理结果，
任务 ID 属于单次服务生命周期。缓存尚未自动清理；停止服务后可删除整个缓存目录。
GEM5 stats 每切片从 NFS 读取一次，以 1 MiB 读块收集最终窗口；RTL 同样读取一次原文。
原始计数器使用最终窗口并以较低 gzip 压缩级别缓存。YAML 处理读取本机临时副本，避免再次读 NFS；
RTL 副本保留完整原文，以保持现有解析器的多次匹配累加行为。
该临时目录在处理结束/取消后清理，全量运行处理期间需要若干 GB 本地临时空间。
原始归档仍依赖大小/修改时间作为变更检测，非内容校验和。

## 统一 JSON API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/analysis/health` | 服务和并发配置 |
| GET | `/api/analysis/resolve?run=<ID或URL>` | 返回同一 Run 的归档候选及 opaque archive ID |
| POST | `/api/analysis/compare` | 提交 A/B 查询，返回 job ID 和排队状态 |
| GET | `/api/analysis/jobs/<job-id>` | 轮询状态、进度、耗时；完成后包含 result、来源、warnings、rows |
| POST | `/api/analysis/jobs/<job-id>/cancel` | JSON `{}`；停止排队/执行中的分析，最终状态为 `cancelled` |
| GET | `/api/analysis/metrics?snapshot=<ID>&q=<搜索词>&group=<Group>` | 搜索指标；snapshot 可重复两次，支持 offset/limit |
| GET | `/api/analysis/jobs/<job-id>/rows.csv` | 导出该查询全部筛选结果 |

`compare` 请求示例，a/b 取自 resolve 的 `candidates[].id`：

```json
{
  "a": "<archive-id-A>",
  "b": "<archive-id-B>",
  "metrics": ["cpi", "raw:system.cpu.commit.branchMispredicts"],
  "groups": [],
  "level": "benchmark",
  "benchmarks": ["gobmk"],
  "simpoints": [],
  "sort": "abs_delta",
  "min_change": 0
}
```

`metrics` 是实际查询列表；`groups` 记录页面分类选择并用于目录筛选，不会排除手动选择的其他指标。
metrics API 的 `group_metrics` 返回每个分类的完整指标列表，独立于搜索文本和分页。

`level` 可选 `benchmark` / `simpoint`。排序默认 `abs_delta`（`|B − A|` 降序），可选 `abs_pct` / `pct` / `delta`（带符号差值降序）/ `name`。
结果行包含 `name`、`benchmark`、`metric`、`a`、`b`、`delta`、`delta_pct`、
`coverage_a/b`、`weight_a/b`、`status`。网页、CLI 和未来 MCP 都应消费这套结果，
不在浏览器另写加权或比较算法。当前尚未注册 MCP server。

CLI 通过同一个 HTTP API 查询：

```bash
python internal/query.py resolve 42178648763
python internal/query.py compare <archive-id-A> <archive-id-B> \
  --metric cpi --metric raw:system.cpu.commit.branchMispredicts
```

## 验证和当前边界

```bash
GEM5_DATA_PROC=/nfs/home/yanyue/codex-tasks/gem5-performance-platform/gem5_data_proc \
  PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s internal/tests -v
npm run build
npm run build:internal
node --test tests/metric-selection.test.mjs
```

测试覆盖 Run 身份、零值/缺失处理、跨 profile 切片、筛选排序、缓存失效、排队合并及限制、
原始计数器搜索、静态文件访问边界、排队取消、真实子进程取消与未完成快照清理，
以及用真实处理器执行的小型加权算分集成测试。
浏览器还验证了刷新后恢复同一任务、停止分析和取消后重新提交。

真实数据验证覆盖 SPEC06 单线程 GEM5 解包 `spec_all/` 与 RTL 报告，以及两者混合查询。
RTL Run `42495637874` 的 145 切片与原版 `run.py -j <同一 profile> -g all` 逐列比较；
GEM5 Run `42590922590` 与迁移前已缓存结果比较。结果与限制记录在项目根目录的 `VALIDATION-20261110.md`。
RTL 路径默认允许 `/nfs/home/ci-runner/perf-report`、`perf-report-custom` 和 `/nfs/home/cirunner/perf-report`，
profile 默认允许 `/nfs/home/share/checkpoints_profiles`、`/nfs/share/zyy`；可用重复的
`--rtl-report-root` / `--profile-root` 显式替换各自列表。接口识别 SPEC17/26，
仍需各自真实 CI 验证。SMT 成对 workload、Trace、仅有压缩包的老归档，以及历史 attempt
的精确绑定暂未接入。NFS 已清理的历史 Run 会明确报错。

## node037 组内试用部署

试用地址：`http://172.19.20.37:8002/?analysis=1`。同组内网可直接访问。
群说明见 [TEAM-TRIAL.md](TEAM-TRIAL.md)。当前以单个 Python 服务提供页面与 API；
`tmux` 负责保持运行，不增加 Nginx 或第二份共享缓存写入进程。
该服务没有登录系统，访问范围依赖现有内网。

本地 checkout：

- Dashboard：`feature/gem5-on-demand-analysis`
- gem5_data_proc：`feature/raw-simpoint-query`

在 node037 上启动（先确保同端口旧服务已停止，且没有运行中的分析）：

```bash
cd /nfs/home/yanyue/codex-tasks/gem5-performance-platform/dashboard
npm ci
npm run build:internal
tmux new-session -d -s gem5-performance -c "$PWD" \
  'exec ./internal/run_team.sh --bind 0.0.0.0 --port 8002 >> internal/local/team.log 2>&1'
```

查看服务、日志和健康状态：

```bash
tmux attach -t gem5-performance
tail -f internal/local/team.log
curl http://127.0.0.1:8002/api/analysis/health
```

在 tmux 中按 `Ctrl-b d` 退出终端，服务继续运行。停止可用
`tmux kill-session -t gem5-performance`，应先等分析完成或在网页停止任务。
代码更新后重建内部页面，再重启同一个 session。缓存保留在 `internal/local/gem5/`，
重启后可复用有效快照，但旧任务 ID 不恢复。机器重启后需要手动重启 tmux 服务。

`internal/run_team.sh` 默认监听 `127.0.0.1:8002`，组内直连启动时显式传上述 bind。
处理器默认使用相邻 `../gem5_data_proc`，也可用 `GEM5_DATA_PROC` 指定。
并发保持一个完整分析任务、四个切片提取 worker；先观察组内使用情况再调整。
