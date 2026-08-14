# 仿真测试结果对比工具

从 PGSim 平台拉取仿真评测结果，对比 **base**（基线版本）与 **test**（待测版本）的差异，
输出带明细和汇总统计的 Excel 报表，用于日常回归分析。

支持多项目并行维护配置：广汽(GAC)、上汽(SAIC_IP5PM / SAIC_AS5PR)、北汽(BAIC)。

---

## 1. 环境准备

```bash
cd /home/didi/phigent/testreport
python3 -m venv .venv                          # 已有 .venv 可跳过
.venv/bin/pip install -r requirements.txt
```

依赖：`pandas`、`openpyxl`、`requests`。验证环境为 Python 3.12.3（见 `requirements.txt`）。

---

## 2. 快速开始

```bash
# 列出配置里的所有项目（不发起对比，用来确认配置是否正确）
.venv/bin/python get_simresult_compare_J6B.py -l

# 运行默认项目（由 sim_config.json 的 default_project 决定，当前是 SAIC_AS5PR）
.venv/bin/python get_simresult_compare_J6B.py

# 只运行指定项目
.venv/bin/python get_simresult_compare_J6B.py -p GAC

# 运行配置里的所有项目，各自输出一个 Excel
.venv/bin/python get_simresult_compare_J6B.py --all
```

| 参数 | 说明 |
|---|---|
| 无参数 | 运行 `default_project` |
| `-p / --project KEY` | 只运行指定项目，与 `--all` 互斥 |
| `-a / --all` | 运行全部项目 |
| `-l / --list-projects` | 列出可用项目后退出 |
| `-h / --help` | 帮助 |

**退出码**：`0` 成功；`1` 配置错误 / 未知项目 / 未生成任何文件；`2` 命令行参数错误。
适合直接挂定时任务，失败可被感知。

---

## 3. 日常使用流程

每天平台上跑完一轮仿真后，需要更新配置里的两组任务 id：

**第 1 步**：在 PGSim 上找到本轮 base 与 test 的仿真任务，记下**最大的那个 simTaskId** 和任务个数。

**第 2 步**：用辅助脚本生成递减的 id 列表：

```bash
.venv/bin/python iterate_grading_tasks_forward.py --start 195154 --count 29
# 输出: [195154, 195153, 195152, ..., 195126]
```

**第 3 步**：把输出粘贴到 `input/sim_config.json` 对应项目的 `base_sim_task_ids` /
`test_sim_task_ids`。

**第 4 步**：运行对比，Excel 会生成到 `output/`。

> **注意 id 的种类**：配置里填的是 **simTaskId**（仿真任务 id，形如 `195153`），
> 不是 gradingTaskId（评测任务 id，形如 `75048`）。工具会自己按 simTaskId
> 查出对应的 gradingTaskId。

---

## 4. 配置文件 `input/sim_config.json`

```jsonc
{
  "projects": {
    "SAIC_AS5PR": {
      "description": "上汽",                    // 仅用于日志显示
      "output_prefix": "SAIC_AS5PR",            // 输出文件名前缀
      "base_sim_task_ids": [194944, 194943],    // 基线版本的 simTaskId 列表
      "test_sim_task_ids": [195153, 195152],    // 待测版本的 simTaskId 列表
      "detail_sheet_fallback": "其他",           // 未匹配到分组的专项归入哪个 sheet
      "detail_sheet_groups": [                  // 把多个专项明细合并成少量 sheet
        {
          "sheet_name": "泊车worldsim测试详情",
          "title_keywords": ["APA泊入状态机", "MEB", "RSPA"]
        }
      ]
    }
  },
  "default_project": "SAIC_AS5PR"               // 不带参数运行时跑哪个项目
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `output_prefix` | 是 | 输出文件名前缀 |
| `base_sim_task_ids` | 是 | 基线版本 simTaskId 列表，为空则跳过该项目 |
| `test_sim_task_ids` | 是 | 待测版本 simTaskId 列表，为空则跳过该项目 |
| `description` | 否 | 日志里显示的中文名 |
| `detail_sheet_groups` | 否 | 明细 sheet 合并规则；不配则每个专项单独一个 sheet |
| `detail_sheet_fallback` | 否 | 兜底 sheet 名；不配或名字不在分组里则用第一个分组 |
| `default_project` | 否 | 顶层字段，裸跑时的默认项目 |

缺必填字段的项目会**告警并跳过**，不影响其它项目。

### base / test 是怎么配对的

工具不按下标配对，而是**按任务名（taskName）配对**：分别在 base 和 test 两侧按 simTaskId
找出任务，再把**任务名完全相同**的两个任务配成一对。所以两个列表的顺序、长度不必严格对应，
但任务名必须一致，名字对不上的专项会被静默丢弃（日志里会打印两侧的任务名，便于排查）。

### `detail_sheet_groups` 的匹配规则

用任务标题去匹配 `title_keywords`，命中**最长**关键词的分组优先 ——
这样 `MEB` 就不会把本该进「泊车性能」的 `MEB性能测试` 抢走。
关键词等长时按配置顺序优先。全都没命中则归入 `detail_sheet_fallback` 并打一条告警。

---

## 5. 认证 `input/auth.txt`

**当前平台对本工具用到的接口开放匿名访问，无需配置，可以直接跑。**

如果将来接口收紧、开始返回 401，把 token 写进 `input/auth.txt` 即可（`Bearer ` 前缀可省略，
工具会自动补）：

```
eyJhbGciOiJIUzI1NiIs...
```

> ⚠️ 该文件不存在、或内容为空时，工具会以匿名方式访问 —— 这是**正常且当前可用**的路径。
> 但**不要写入一个空 token**：平台对 `Bearer ` 后面没有内容的请求会直接返回
> 401「用户认证失败」，反而不如不带。

---

## 6. 输出说明

文件写到 `output/`，命名为 `{output_prefix}_{YYYYMMDD}.xlsx`，例如
`output/SAIC_AS5PR_20260814.xlsx`。同一天重复运行会覆盖当天的文件。

### 明细 sheet

每个 sheet 是一类专项的场景级对比，只保留 base 与 test **共有**的 `scenarioId`（内连接）。

| 列 | 说明 |
|---|---|
| `专项名称` | 该行来自哪个专项任务（仅在按 `detail_sheet_groups` 合并后出现） |
| `scenarioId` / `scenarioName` | 场景 id 与名称 |
| `base_gradingResult` / `test_gradingResult` | 两个版本的评测结果（`pass` / `fail` 等） |
| `base_failedReason` / `test_failedReason` | 失败原因 |
| `base_sim_url` / `test_sim_url` | 仿真回放链接，可直接点开 |
| `对比` | `base结果 -> test结果`，如 `pass -> fail`，**筛这一列即可找出回归场景** |
| `测试结果分析` / `截图` | 空列，留给人工填写结论 |
| `新增_fail_metrics` | 占位列，目前恒为 `0`（见第 8 节） |
| `base_*_metrics` / `test_*_metrics` | 两个版本各自的 pass / fail / unknown 指标名列表 |

### 汇总 sheet（`汇总`）

每行一个专项：

| 列 | 含义 |
|---|---|
| `专项名称` | 从任务标题截取 |
| `base_pass` / `base_fail` | base 侧通过 / 失败的场景数 |
| `base_pass_rate` | `base_pass / (base_pass + base_fail)`，取整百分比 |
| `test_pass` / `test_fail` | test 侧通过 / 失败的场景数 |
| `test_pass_rate` | 同上，test 侧 |
| `diff_pass_rate` | 两个通过率之差（用未取整的原值相减后再取整），**正数表示本轮变好** |
| `pass->fail` | **回归数**：base 通过、test 失败 —— 最需要关注 |
| `fail->pass` | 修复数 |
| `pass->pass` / `fail->fail` | 保持通过 / 保持失败 |

---

## 7. 代码结构

```
get_simresult_compare_J6B.py      # 入口（薄封装）
iterate_grading_tasks_forward.py  # 生成连续 simTaskId 列表的小工具
requirements.txt
simcompare/                       # 主体实现
├── api.py       # PGSimClient：接口调用、超时、请求头
├── config.py    # sim_config.json / auth.txt 的读取与校验
├── compare.py   # 指标分类、明细表构建、版本对比、通过率统计、任务配对
├── sheets.py    # 明细 sheet 分组与 Excel 写出
└── cli.py       # 命令行参数与整体编排
tests/
└── test_compare.py               # 41 个离线单元测试
input/
├── sim_config.json               # 项目配置（必需）
└── auth.txt                      # 认证 token（可选，见第 5 节）
output/                           # 报表输出目录（已在 .gitignore 中）
```

### 运行测试

```bash
.venv/bin/python -m unittest discover -s tests -t . -v
```

测试全部离线，不发网络请求，只用标准库 `unittest`（无需 pytest）。
`compare.py` 里的函数都只接收数据、不发请求，因此可以直接单测。

---

## 8. 已知情况与注意事项

- **`新增_fail_metrics` 恒为 0**：占位列，尚未实现「test 相比 base 新增了哪些 fail 指标」的计算。
  若需要，可由 `test_fail_metrics` 减 `base_fail_metrics` 得出。
- **汇总表的 `专项名称` 区分度低**：取的是任务标题去掉前两段和最后一段的部分，
  对 `SAIC_J6B_AS5PR_daily_行车ACC-默认grading` 只会截出 `AS5PR_daily`，同一项目下各行会重复。
- **任务 id 会过期**：平台上较早的任务明细可能已被清理，此时该专项会因「数据获取失败」
  或「没有共同的场景」被跳过并打告警 —— 属正常现象，更新配置里的 id 即可。
- **某项目无输出**：先看日志里的「未找到匹配的任务对」，通常是两侧任务名不一致，
  或 id 填错了种类（填成 gradingTaskId 而非 simTaskId）。
- **Excel 工作表名上限 31 字符**：超长会被截断，截断后重名会自动加 `_2`、`_3` 后缀并告警。
- `output/` 下形如 `.~xxx.xlsx` 的文件是 Excel 打开时留下的锁文件，与本工具无关，可以删除。
