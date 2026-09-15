# EvidenceCraft · 单机报告生成工作台

输入一个主题或一棵结构树 → 系统规划取数 → LLM 分节写作 + 逐数对账 + 评审修订循环 → 产出带图表的 HTML / DOCX 报告。单机单用户，FastAPI 后端 + Vue3 免构建前端，LLM 走 OpenAI 兼容接口。

```
python -m uvicorn server.main:app --port 8765     # 启动后浏览器打开 http://127.0.0.1:8765
```

## 核心特性

- **结构树驱动**：报告骨架是一棵「结构树」（章节 + 每节写作要求 + 数据需求）。三种起点：内置示例复制、对话生成、现有树复用；树有版本快照 / 回滚 / 派生 / lint 检查。
- **数据先行**：按树的数据需求自动生成采集计划（检索 / 联网 / 数据库 / 本地资料夹），缺口回执逐条裁决（去搜 / 我提供资料 / 定性写 / 砍掉），预检只跑数据层并可缓存复用。
- **生成即治理**：正文每个数字必须对账到事实包（无出处数字会被拦截重写）；规则校验 + LLM 五维评审，不合格自动退回修订（缺省 2 轮，可配）；残句 / 数据稀薄节有确定性防线。
- **反馈迭代**：报告生成后仍可改——一段自然语言总意见，系统解析成三通道操作（文风重写 / 取数重写 / 树结构调整），逐轮留痕、可回滚。
- **图文同源**：图表由 matplotlib 按事实 / 表格数据确定性渲染（模型不画图）；渲染失败会在报告详情页给出原因。
- **文风卡**：规则 + 范文节选，模板页可视化管理（内置三张只读，自定义卡增删改即时生效）。

## 快速开始

环境：Python ≥ 3.10（实测 3.13）。

```bash
pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml   # 编辑 model 段：base_url / api_key / model
python -m uvicorn server.main:app --port 8765
```

`config/settings.yaml` 已 gitignore，密钥只放这里，不入库。

## 界面导览

| 页面 | 路由 | 用途 |
|---|---|---|
| 新建报告 | `#/new` | 三步向导：① 选起点出结构树（示例 / 对话 / 现有树）→ ② 配数据（出计划 → 缺口裁决 → 预检 → 确认；或直接给意图 / 本地资料夹）→ ③ 生成（SSE 实时进度，断线自动轮询恢复，完成自动跳详情） |
| 报告库 | `#/reports` | 历史报告列表，按树过滤；点进详情 |
| 报告详情 | `#/reports/<目录名>` | 五页签：预览（HTML）/ 反馈迭代 / 治理体检（judge 评分、逐节对账、图表状态、修订轨迹）/ 文件 / 元信息 |
| 模板工作台 | `#/templates` | 树列表（lint 徽标、派生、删除）、结构编辑器、对话改树（同树版本 +1）、文风卡管理 |
| 设置 | `#/settings` | 模型与密钥配置（界面改即落盘 settings.yaml） |

## 命令行（与界面等价，便于脚本化 / 复跑）

```bash
# 树模式：全流程（计划需先在界面出好并确认，plans/ 下的文件名）
python run_pipeline.py --full --tree <树id> --plan <计划文件名> [--folder <资料夹>] [--intent <意图>]

# 只跑数据层看事实；或复用某次运行的数据层秒进写作（调模板/规则时省取数）
python run_pipeline.py --data-only --tree <树id> --intent "……"
python run_pipeline.py --full --type company_review --stock 000803 --reuse-data <目录名>
```

树模式无任何取数来源（plan / intent / folder 全空）会拒绝生成；确需全定性报告加 `--allow-qualitative`。

## 目录结构

```
server/            FastAPI 路由（runs / trees / types / sources / settings / overview）
static/            免构建前端：index.html + js/app.js + js/views/*.js（Vue3 全局构建 + naive-ui CDN）
pipeline/          写作流水线：outline / sections / reconcile(对账) / validate / judge / revise / feedback / stylecards
datalayer/         数据层：adapter 插件（rag/web/db/xlsx…）+ planner（采集计划）+ corpus（本地语料）
trees/             树存储（版本快照/操作日志/lint）
template_factory/  Spec v2 schema：报告结构（章节/表格/图表模板/校验区间）
render/            final.html / final.docx / matplotlib 图表
run_pipeline.py    CLI 入口（界面生成走同一函数）
config/
  settings.yaml          运行配置（gitignore）
  trees/<id>/            每棵树：tree.yaml + versions/ + plans/ + ops_log.jsonl
  tree_templates/        内置示例树（只读）
  style_cards/           文风卡 YAML
  report_types/          经典报告类型（后端兼容保留，工作台 UI 已下线）
artifacts/         每次运行一个目录：facts / outline / sections / 各评审 json / final.html / final.docx / 图表
tests/             pytest（98 项，全部 mock LLM，不烧真实调用）
```

## 测试与文档

```bash
python -m pytest tests/ -q
```

- [API.md](API.md) — HTTP 端点契约（含树模式全部端点与错误码）
- [修改方案V3.md](修改方案V3.md) — 最新批次的规格与实施记录（附录 A 逐项核对表 / 附录 B 偏差记录）；更早的 V2 改造见 git 提交历史

## 工程约定（改代码前必读）

- **免构建前端链**：禁 node / npm / 打包器；Vue3 运行时模板编译（`EC.views['/路由']`），前端改动只需递增 `static/index.html` 里的 `?v=` 版本号。注意：模板字符串插值里写 `\n` 会被编译器当真实换行导致整页渲染失败，必须写 `\\n`。
- **树模式 API 只增不改**；经典模式（`config/report_types/`）后端冻结；反馈回路协议稳定。
- **治理硬线**：对账 / 校验在每轮修订与每轮反馈后全量重跑，不得旁路；生成门禁（无取数来源拒绝生成）不可关闭。
- **密钥**只进 `config/settings.yaml`（gitignore）；对外 API 响应一律掩码。
- **单测一律 mock LLM**（`pipeline.llm.chat_json`），不烧真实调用。
- 中文文件读写一律 UTF-8。
