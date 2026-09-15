# EvidenceCraft API 契约（M6 冻结）

> M8 前端照此契约对接；修改任何签名须同步本文件。
> 类型标注：`SpecV2` = template_factory.schema.SpecV2；`doc` = facts.json 结构。

## 数据形状

```jsonc
// 事实六元组（全链路引用句柄；reliability 为 M7 新增可选字段）
{"id": "fin.2026Q2.revenue_yi", "name": "营业收入", "value": 7.3538,
 "unit": "亿元", "source": "eastmoney/datacenter/...", "as_of": "2026-08-25",
 "reliability": "authoritative|retrieved|manual"}

// 样例解析块（template_factory.parsers 产物）
{"type": "heading|para|table|figure", "level?": 2, "text?": "...",
 "n_chars?": 188, "header?": [...], "sample_rows?": [...], "caption?": "..."}
```

## pipeline（阶段函数，spec 贯穿传递）

| 函数 | 契约 |
|---|---|
| `schema.load_spec(path=None) -> SpecV2` | 加载即 pydantic 校验；缺省 company_review.yaml |
| `outline.build_outline(doc, spec) -> dict` | `{title, views:[{slot_id,heading,cited_fact_ids,guidance}], slot_briefs}` |
| `sections.gen_view(doc, view, spec) -> dict` | `{heading, body, cited_fact_ids, slot_id}`；引用自动规范化、空引用走 spec fallback 声明 |
| `sections.gen_forecast_note(doc, spec) -> dict` | 表格由 `render_table` 代码渲染，LLM 只写说明 |
| `sections.gen_risks(doc, spec, views=...) -> dict` | 按 spec 风险章节 strategy/style |
| `sections.render_table(doc, spec) -> str` | markdown 表（`tables[].renderer` 分发） |
| `rating.rule_rating(doc) -> str` | 股票部门专属；M7 收进部门配置 |
| `reconcile.reconcile(doc, outline, sections, forecast, risks, spec) -> dict` | `{status: pass|warn|fail, checks:[{section, unknown_numbers, cited_missing, ignored_citations, ...}]}`；数字按绝对值容差 0.5% 匹配 |
| `reconcile.norm_citation(c) -> str|None` | 引用规范化：剥 `[]`、裸公告号补 `ann.` 前缀 |
| `validate.run(doc, outline, views, forecast, risks, rating, spec, crosscheck=None) -> dict` | 参数全部来自 spec；body_len FAIL 项携带 `metric` 数值 |
| `judge.run(doc, outline, views, forecast, risks, reconcile_report, validate_report, spec) -> dict` | 五维评分；对账未匹配与 validate FAIL 项代码级强制合成 issues |
| `revise.apply(doc, outline, written, forecast, risks, judge_report, spec) -> tuple` | 仅重写点名节；target 按位置兜底映射 |
| `render.html_report.render(doc, outline, sections, forecast, risks, spec, rating, kline_png=None) -> str` | 章节 kind 驱动版式；disclaimer 来自 spec |
| `run_pipeline.main(argv=None, progress=None)` | `progress(stage, message, data|None)`；stage ∈ data/outline/sections/review/render；M8 SSE 直接转发 |

## template_factory（M6 新增）

| 函数/CLI | 契约 |
|---|---|
| `parsers.parse(path) -> dict` | docx/pdf/md/txt → `{source, kind, blocks}`；扫描版 PDF 不支持 |
| `parsers.skeleton(parsed) -> str` | 块结构 → 紧凑骨架文本（结构提取输入） |
| `parsers.section_text(parsed, heading) -> str` | 标题（模糊匹配）下的正文块文本 |
| `python -m template_factory.extract --samples A.docx B.pdf --out x_draft.yaml [--compare 手写.yaml]` | 六步提取 → Schema 校验通过的草案 + `<out>.extraction_report.md`（置信度/分歧/范文选段/待人工裁决）+ `<out>.extractions.json`（中间草案） |
| `python -m template_factory.replay --spec x.yaml --sample 样例 [--rounds 2]` | 样例数字抽成临时事实（reliability=manual）→ 迷你大纲 → 复用④⑤回放；判定 PASS / PASS_WITH_WARN（字数±15%内）/ FAIL（未对账数字、缺失引用、字数越界）；多轮取最好 |

## datalayer 数据源插件化（M7 新增）

| 函数/CLI | 契约 |
|---|---|
| `registry.run_department(department, run_params) -> (doc, crosscheck)` | 按部门 profile 的绑定列表逐个调 adapter，汇总 facts_doc；单源失败记 warning 不阻塞；`$` 引用解析（`$运行参数` / `$vocabulary.x` / `$ctx.x`，dict/list 递归） |
| `registry.load_profile(department) -> dict` | 读 `config/departments/{dept}/profile.yaml` |
| `registry.ADAPTERS` | 注册表：adapter key → 类；新 adapter 继承 `SourceAdapter`（key/kind/fetch）放入 `datalayer/adapters/` 即自动注册 |
| `adapters.base.AdapterResult` | `{facts, collections, meta, ctx, warnings}`；ctx 供跨 binding 传递（如 board_code） |
| `adapters.base.rows_to_facts(...)` | 行记录 → 事实六元组（local_file/database 共用声明式映射） |
| `database.sanitize_sql(query, limit=200) -> str` | **SQL 安全硬约束（代码强制，不靠提示词）**：剥注释后仅允许单条 SELECT；引号外出现分号（多语句）拒绝；ATTACH/PRAGMA 等非查询关键字拒绝；无 LIMIT 自动追加 `LIMIT 200`。SQLiteAdapter 执行前统一过此函数（静态与动态查询双保险），违规抛 ValueError → registry 记 warning |
| 内置 adapter | web：`em_quote / em_financial / em_peer / em_announcement / em_industry_news / em_consensus / em_mainop / em_statements / em_news`、`usgs_earthquakes`（公开 API 验证）；local_file：`xlsx_table`（声明式映射 + inbox 目录 + 内容 md5 指纹）；database：`sqlite_query`（查询模板 + 命名参数，经 sanitize_sql 安全约束）；rag：`rag_client`（外部检索客户端 + 片段抽数，`reliability=retrieved`，**抽取即对账**：数字必须在片段原文中找到，否则丢弃；mock 模式内置 2-gram 关键词检索，片段可由 `python -m datalayer.corpus` 建库）；web：`web_search`（keyless 联网搜索：百度→搜狗→Bing RSS→DDG 级联 + playwright 连本机 Edge 兜底 + settings.web_search.endpoint 付费 API 预留；事实卡带 URL/发布日期，抽取回验原文，结果缓存 data/cache/web_search/） |
| 部门 profile 字段 | `bindings[{need, adapter, params}]`、`vocabulary`、`features`（如 kline_chart）、`crosschecks`、`judge_reference`（部门级缺省，模板可覆盖）、`params_schema` |
| 流水线 CLI | `python run_pipeline.py --full --type <dept> [--stock X] [--project P] [--period T] [--spec xxx.yaml] [--intent 一段话意图] [--folder 资料目录] [--reuse-data 目录名] [--model-tier fast\|quality]`；`--model-tier` 固定本次全部 LLM 调用走该档（settings.model_tiers 档名，写入 meta.json 的 model_tier）；judge 范文解析链：spec → profile |
| `registry.run_data_layer(type_id, run_params, extra_bindings=None, bindings_override=None)` | extra_bindings 追加动态绑定；bindings_override 整体替换静态绑定（意图规划接管查询时用），其余行为不变；绑定执行无 `$ctx` 依赖时分批并行（≤6/批） |
| `planner.make_plan(spec, sources, intent, folder=None) -> plan` | **意图驱动数据规划器（Phase B / F1）**：一段话意图 + 结构模板 + 静态绑定 → 采集计划 `{mode: llm\|fallback, subject, focus, rag[], web[], db[], tables_kept[], corpus?, warnings?}`；db 规划：读 settings.databases 各 SQLite 库的表/列 schema（PRAGMA，只读结构不取数）喂给 LLM 产出 `db[{need, db_ref, query, query_params, id_column, name_template, value_columns, as_of_column?, table_columns?, table_id?}]`，逐条过 sanitize_sql（违规拒绝并记 warnings）；静态 db 绑定不可剔除；LLM 规划失败自动规则兜底（沿用模板查询，plan 标注 fallback 与 error，不臆造 db 查询）；folder 给定时现场建语料库（内容指纹缓存 data/corpus/_intent/<fp>/，未变化复用），rag 查询切换到新库；plan.json 随运行落 artifacts 可审计（含 db 查询全文） |
| `planner.plan_to_bindings(plan, sources) -> bindings` | 计划 → sources.yaml 同 schema 绑定列表（rag/web 按计划生成、db 按 db[] 转 sqlite_query 动态绑定（query_params 值一律字符串化，可为 `$参数` 引用运行参数）、表格按 tables_kept 沿用），配合 run_data_layer(bindings_override=...) 执行 |
| 意图模式 CLI | `python run_pipeline.py --full --type hp_quartz_review --project 主题 --intent "一段话意图" [--folder "本地资料目录"]`；意图焦点写入 facts meta.intent 并进入大纲选材；不传 --intent/--folder 时行为与原版完全一致 |
| `POST /api/types/{id}/extract` | 提取结果落 `report.draft.yaml` 草稿（不直接定稿）；已有定稿 report.yaml 时 409 |
| `GET /api/types/{id}/structure-draft` | `{exists, spec}`：提取草稿（范文结构待确认） |
| `POST /api/types/{id}/structure-confirm` | body `{spec?}`（缺省用草稿原样）→ SpecV2 校验 → 定稿 report.yaml；已定稿 409，无草稿 404 |
| `DELETE /api/types/{id}/structure-draft` | 放弃草稿 |
| `GET /api/types/{id}` | 新增 `has_draft` / `draft_spec` 字段 |
| `POST /api/runs/start` | body 新增 `intent`（写作意图）/`folder`（本地资料目录）/`model_tier`（F3 档位覆盖），透传 run_pipeline `--intent/--folder/--model-tier` |
| `POST /api/runs/preview` | body 新增 `intent`/`folder`/`model_tier`（仅本次预检生效）：意图模式先规划采集计划再按计划预检，响应新增 `plan{mode,focus,n_rag,n_web,n_db,n_tables,corpus,warnings?,note?}` |

## server（M8-R1 重构——单用户"报告类型"模型）

界面实体：**报告类型 = 报告结构（report.yaml，Spec v2）+ 数据来源（sources.yaml：
name/status/params_schema/vocabulary/features/crosschecks/judge_reference/bindings）**。
目录：`config/report_types/<id>/`（report.yaml、sources.yaml、versions/report/ 版本留痕
（保留 20 份）、samples/、parsed/、chat.json、replay_history.json）。
流水线按 `--type <id>` 取 spec 与绑定；每次生成 meta.json 记录
`type_id / template_fingerprint（report.yaml sha1 前 12 位）/ params`。

启动：`python -m uvicorn server.main:app --port 8765`；前端 `static/`（Vue3 +
NaiveUI + ECharts，vendor/ 本地化，免构建）；静态产物挂 `/artifacts/<dir>/`。
SSE 事件协议：`{type: progress|end, stage, message, data|None, ts}`，end 携带
`{status: done|error|cancelled, run_dir, error, error_detail}`；data 恒含
`llm_calls / llm_seconds / stage_seconds / llm_tiers`（F3 分档调用统计：
`{档名: {calls, seconds, model}}`，档名 default/fast/quality）；600s 心跳。

| 路由 | 契约 |
|---|---|
| `GET /api/health` · `GET /api/overview` | 健康摘要（key 掩码）/ 首页汇总 `{types, n_types, recent_runs, model}` |
| `GET /api/jobs/{job_id}` | 任意后台任务状态：`{status, error, error_detail, result}` |
| `GET/PUT /api/settings/model` · `POST .../test` | 模型配置（api_key 只写不回显）；PUT 热生效；test → `{ok, latency_s, reply\|error}` |
| `GET/PUT /api/settings/pipeline` | `judge_threshold`（默认 36）/ `revise_rounds`（默认 2），judge 与流水线消费 |
| `GET/PUT /api/sources/connections` · `POST /test/database` · `POST /test/rag` | 全局连接（databases/rag）；SQLite 只读连通；RAG mock/live 探测 |
| `GET /api/sources/adapters` | 适配器注册表 `[{key, kind, reliability, summary, param_schema, ctx_keys}]`——summary/param_schema/ctx_keys 为适配器类自声明（参数表单、$ctx 提示与顺序校验的数据源） |
| `GET/POST /api/types` · `GET/PUT .../sources` · `POST /{id}/status` · `POST /{id}/copy` · `DELETE /{id}` | 报告类型 CRUD + 状态机（draft→verified 需最近回放通过；verified→published 需有 report.yaml） |
| `POST /api/types/load-demo` | 从 `config/demo_types/` 载入演示报告类型（已存在则跳过） |
| `POST /api/types/{id}/extract`（multipart files[]） | `extract.run()` 线程执行 → `{job_id, events_url}`；样例与解析缓存写入类型目录 |
| `GET /api/types/{id}` · `/sample/{file}` · `/jobs/{job}/events` | 全量详情 / 样例解析树 / 任务 SSE |
| `PUT /api/types/{id}/report`（JSON）· `/report-yaml`（文本） | 结构保存：SpecV2 校验 → 版本快照 → 落盘 → 字段级 diff |
| `POST /api/types/{id}/patch {message}` · `/apply {ops}` | 对话 patch（patchlib：set/del/insert，路径 `sections[0].x`）：校验不落盘 → diff；apply 校验+快照落盘 |
| `POST /api/types/{id}/replay {sample, rounds}` | 回放（多轮取最好），**追加 replay_history**（判定/字数合规/未对账数）；PASS/PASS_WITH_WARN 且为草稿 → 自动置 verified |
| `POST /api/types/{id}/dryrun {params}` | 真实数据试跑一节，结果落 dryrun_result.json |
| `POST /api/types/{id}/bindings/test {index, params}` | 单绑定测试：$ctx 链累积执行 → `{ok, n_facts, resolved(解析后实际参数), warnings, sample}` |
| `GET /api/types/{id}/versions` · `/versions/diff?a&b` · `POST /versions/rollback` | 版本时间线 / unified diff / 回滚（回滚前自动留痕） |
| `POST /api/runs/start {type_id, stock?, project?, period?}` | 工作线程跑流水线；`cancel_event` 支持取消（阶段边界干净退出，产物写 `_cancelled` 标记） |
| `POST /api/runs/preview {type_id, params}` | 数据预检：只跑数据层 → `{ok, n_facts, warnings, crosscheck, sample}` |
| `POST /api/runs/{id}/cancel` · `GET /api/runs/{id}` | 取消 / 状态（含 error_detail） |
| `GET /api/runs?type_id=` · `/artifact?dir&file` · `/report?dir&format=html\|docx` · `DELETE /{dir}` | 历史（可按类型筛选）/ 产物 JSON（防穿越）/ 报告下载 / 删除运行 |

安全约定：凭据只进 settings.yaml（gitignored），API 响应密钥一律掩码；
report.yaml 每次保存前自动快照（versions/report/，保留 20 份）；artifacts 路径
参数白名单校验；任务/运行错误返回"人话摘要 + error_detail 完整堆栈"。

## F1~F4 功能增强（2026-09-11）

| 项 | 契约 |
|---|---|
| F3 模型分档 | settings.yaml 可选段 `model_tiers: {fast: {...}, quality: {...}}`（每档只写覆盖字段 base_url/api_key/model/reasoning_effort，缺省继承 model 段）+ `tier_roles: {extract: fast, write: quality}`（extract=rag/web 抽取与规划器，write=大纲/分节/judge/修订）；两段都不配置时行为与单模型完全一致。`pipeline.llm`：`chat_json(..., tier=档名)`、`tier_for(role)`、`client(tier)` 按档缓存、`set_tier_override(档名)`（--model-tier 全局覆盖）、`stats()["tiers"]` 分档计数。生成页"模型档位"下拉（默认/快/质量）；前端缓存参数 R12 |
| F1 数据库动态规划 | 见 datalayer 段 `planner.make_plan` / `sanitize_sql` 行：意图规划产出 db 查询（SQLite，schema 经 PRAGMA 读取），安全约束代码强制；plan.json 落查询全文可审计 |
| F2 溯源可点击 | 仅渲染层（不进提示词/对账/字数）：html_report 附录行加锚点 `id="src-{fact_id}"`；text/views/table/risk 章节末尾追加 `<p class="sec-src">本章数据来源：…</p>`（编号为锚点链接，至多 12 条，无引用不显示）；notes 传全量 dict（含 cited_fact_ids），docx 渲染保持现状 |
| F4 图表扩展 | `ChartTemplate.type` 增加 `scatter`（仅 table: 源，x 为数值列，y 多列多序列）与 `hist`（仅 table: 源，y 单数值列频数分布，`bins` 参数缺省 10）；新增字段 `ChartTemplate.bins`；单测 `tests/test_charts.py`；geology_demo_review 内嵌 Au 品位直方图实跑 |


## 产物目录（已稳定，报告详情页直接消费）

```
artifacts/<subject>_<ts>/
  meta.json（type_id/type_name/template_fingerprint/params）
  facts.json / crosscheck_report.json / outline.json / sections.json
  reconcile_report.json / validate_report.json / judge_report.json
  revision_roundN.json / index_kline.png / final.html / final.docx
  _cancelled（取消标记，仅被取消的运行）
```

## server（M8 新增——工作台薄层）

启动：`python -m uvicorn server.main:app --port 8765`；前端 `static/`（Vue3 +
NaiveUI + ECharts，vendor/ 本地化，免构建）；静态产物挂 `/artifacts/<dir>/`。
SSE 事件协议：`{type: progress|end, stage, message, data|None, ts}`，end 携带
`{status: done|error, run_dir, error}`，600s 心跳。

| 路由 | 契约 |
|---|---|
| `GET /api/health` · `GET /api/overview` | 健康摘要（key 掩码）/ 首页汇总 `{departments, templates, recent_runs, model}` |
| `GET/PUT /api/settings/model` · `POST .../test` | 模型配置（api_key 只写不回显）；PUT 热生效（settings.reload+llm.reset）；test → `{ok, latency_s, reply\|error}` |
| `GET/PUT /api/sources/connections` · `POST /test/database` · `POST /test/rag` | databases/rag 读写（ruamel 保注释）；SQLite 只读连通；RAG mock 确认 / live 探测 |
| `GET /api/departments[/{id}]` · `PUT` · `POST` · `POST /{id}/test_binding` | 部门 CRUD（保存自动快照 profile_snapshots/）；单绑定测试：$ctx 链累积执行至目标绑定 → `{ok, facts, collections, warnings, sample}` |
| `POST /api/runs/start {department, stock?, project?, period?, spec?}` | 工作线程跑 `run_pipeline.main` → `{id, events_url}`；progress 回调纯转发 |
| `GET /api/runs/{id}/events` | SSE（上协议） |
| `GET /api/runs` · `/artifact?dir&file` · `/report?dir&format=html\|docx` | 历史（只列含标记产物的目录）/ 产物 JSON / 报告下载；目录名白名单校验防穿越 |
| `POST /api/studio/extract`（multipart files[], report_type?） | `template_factory.extract.run()` 线程执行 → `{job_id, events_url}`；样例解析缓存写 workspace/parsed/ |
| `GET /api/studio/jobs_list` · `/jobs/{id}` · `/jobs/{id}/events` · `/jobs/{id}/sample/{file}` | 任务列表 / 工作区全量（spec+extraction_report+chat+replay+dryrun）/ SSE / 样例解析树 |
| `PUT /api/studio/spec`（JSON）· `/spec-yaml`（全文） | 草案保存：SpecV2 校验 → 快照 → 落盘 → 返回字段级 diff |
| `POST /api/studio/patch {job_id, message}` | 对话 → LLM 结构化 ops（`set/del/insert`，路径 `sections[0].x`）；校验**不落盘** → `{reply, ops, diff, error}` |
| `POST /api/studio/apply {job_id, ops}` | patchlib 应用 + 校验 + 快照落盘 → diff |
| `POST /api/studio/replay {job_id, sample, rounds}` · `/dryrun {job_id, department, params}` | `replay.run_rounds` / `dryrun.run` 线程执行，结果落 workspace 并随 jobs/{id} 返回 |
| `POST /api/studio/finalize {job_id, name}` | 定稿 `config/report_types/{name}.yaml`；同名旧版进 `versions/{name}/` |
| `GET /api/templates` · `/{name}/raw` · `/{name}/versions` · `/{name}/diff?a&b` · `POST /rollback` | 模板库：清单/原文/版本/unified diff/回滚（回滚前自动留痕） |

工作区：`studio_workspace/{job_id}/`（samples/ parsed/ draft.yaml snapshots/
chat.json replay_result.json dryrun_result.json meta.json）。
安全约定（M8 起生效）：凭据只进 settings.yaml，API 响应密钥一律掩码；
spec/profile 每次保存前自动快照；artifacts/workspace 路径参数白名单校验。

## V2 工作台新增端点（2026-09-15 树 id 中文化 + 三阶段改造）

树模式 API 只增不改；经典入口（/api/runs/start、/api/runs/preview、routes_types）
冻结保留语义。静态前端路由：`#/new`（向导）、`#/reports`（报告库）、
`#/reports/:dir`（详情五页签）、`#/templates`（模板页）、`#/settings`；
旧路由（#/overview、#/trees、#/generate、#/types、#/run?dir=）重定向。

### 结构树（/api/trees）

| 路由 | 契约 |
|---|---|
| `GET /api/trees/style-cards/all` | 全部文风卡（含 rules 全文与 excerpts——编辑器预览用） |
| `POST /api/trees/style-cards {id, name, desc?, rules?, excerpts?}` | **V3-C 新增**：新建自定义文风卡，写入即落盘 `config/style_cards/<id>.yaml`（UTF-8，无缓存立即生效）；id 重复 **409**、id 非法（须 `^[A-Za-z0-9_]{1,40}$`）**422** |
| `PUT /api/trees/style-cards/{id}` | **V3-C 新增**：保存自定义文风卡（内置三卡 **403**；不存在 **404**；body.id 与 URL 不一致 **422**） |
| `DELETE /api/trees/style-cards/{id}` | **V3-C 新增**：删除自定义文风卡（内置卡 **403**）；被树引用时 **409** 附引用树清单 |
| `GET /api/trees/templates` | 内置示例树清单（config/tree_templates/）：`{id, name, description, genre, style_card, n_sections}` |
| `POST /api/trees/templates/copy {template_id}` | 示例树 → 复制为可编辑草稿；id 自动去重；provenance `{kind: template}` |
| `POST /api/trees/{id}/copy` | 树派生（F10）：新 id=`原名_派生N`，provenance 记父树与父版本 |
| `PUT /api/trees/{id}` | 保存（新增）：body 可带 `base_version`——乐观锁，不符返回 **409**；lint error（如节标题重复）返回 **422** 拦保存 |
| `POST /api/trees/{id}/chat {message, base_version?}` | 对话改树；base_version 乐观锁共用；返回 `{applied, summary, version, fingerprint, lint, changed}` |
| `POST /api/trees/{id}/plan/confirm` | 确认门槛通过时在计划文件持久化 `confirmed/confirmed_at`（A4 门禁复查依据） |
| `POST /api/trees/{id}/plan/preview {plan_file}` | **F9 预检**：按计划只跑数据层 → `{ok, n_facts, by_source, crosscheck, sample, warnings, fingerprint, cached}`；计划内容指纹（rag/web/db/tables_kept/corpus/mode/**file_bindings**——V3-E6 补）一致时复用 `plans/_preview_<fp>.json` 缓存 |

节 id 约定（V2）：中/英/数字/下划线/连字符；agent 生成与对话加节默认
id=节标题；**报告生成后不要修改 id**（改 id 等于换节，反馈定位失效）。

### 报告运行（/api/runs）

| 路由 | 契约 |
|---|---|
| `POST /api/runs/from_tree {tree_id, plan?, intent?, folder?}` | **A4 生成门禁**：三来源全空 → 422；plan 未 confirmed → 422；plan 有未裁决 gap → 422；plan.needs_folder 且无 folder → 422（A5） |
| `GET /api/runs?type_id=&tree=` | 报告库列表：条目新增 `tree_id`/`source_name`（树名或类型名）；`tree` 过滤同树报告（双向追溯） |
| `GET /api/runs/{dir}/files` | 详情页「文件」页签：根文件 + `rounds/<n>/` 一层清单（后缀白名单） |
| `GET /api/runs/artifact?dir&file` | file 白名单放行 `rounds/<n>/<name>` 三段形式 |

### 反馈回路（语义不变，字段新增）

- judge issues 每项带 `kind: structure|data|style`（F3：结构类不喂 revise，
  修订循环确定性处理——重复节删除/缺表插表格节，只改运行内存不动树）。
- `POST /api/runs/{dir}/judge` 深度评审返回 `method: "median_of_3"`、
  `runs/raw_runs`（三次原始分）、`spread`（各维极差）、`unstable`（极差>4 维度）（F8）。
- `GET /api/runs/{dir}/feedback/rounds` 条目新增 `reconcile/validate`（C3 轮次
  徽标）与 `tree_lint {errors, warnings, items}`（F12 树健康）；回滚条目
  （无 rounds 目录）同样可见。
- 流水线产物新增 `revision_history.json`：`{rounds:[{round, issues, lens}],
  structure_notes, thin_warnings}`（收敛性基线数据源，F1/F3/F4）。

### CLI（run_pipeline.py）

| 参数 | 契约 |
|---|---|
| `--tree X --plan Y` 且计划含 needs_folder | 必须给 `--folder`（否则 SystemExit，A5）；folder 会现场建语料 + 生成 xlsx 绑定 |
| `--tree` 且 `--plan/--intent/--folder` 全空 | SystemExit 门禁；`--allow-qualitative` 显式放行全定性生成（A4） |
| `settings.pipeline` | 新增 `write_temperature`（写作/修订，建议 0.3）、`judge_temperature`（评审，建议 0）——缺省不传保持旧行为（F5/F7）；`section_concurrency`（分节并行，缺省 3，置 1 回退串行，F13）；`revise_rounds` 代码缺省 2→3（F6） |

### Excel 数据接入（F11）

资料文件夹支持 PDF+XLSX 混合：每个 xlsx 生成一条确定性 `xlsx_table` 绑定
（id/table_id=文件名词干、sheet 首个、id_column=首个非空列、value_columns
采样前 50 行识别数值列、unit 取列名括号内容、table_columns 全部列），随计划
`file_bindings` 落盘并进 `plan_to_bindings`；事实层进对账、表格层供
`table:<table_id>` 图表源与 `generic_rows` 表格节；needs_coverage 命中表格
文件名/列名的 gap 自动升级 covered（source=xlsx）。`ensure_corpus` 无 PDF
时返回 None（不再报错）。
