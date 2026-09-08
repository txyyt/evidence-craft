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
| 内置 adapter | web：`em_quote / em_financial / em_peer / em_announcement / em_industry_news / em_consensus / em_mainop / em_statements / em_news`、`usgs_earthquakes`（公开 API 验证）；local_file：`xlsx_table`（声明式映射 + inbox 目录 + 内容 md5 指纹）；database：`sqlite_query`（查询模板 + 命名参数）；rag：`rag_client`（外部检索客户端 + 片段抽数，`reliability=retrieved`，**抽取即对账**：数字必须在片段原文中找到，否则丢弃） |
| 部门 profile 字段 | `bindings[{need, adapter, params}]`、`vocabulary`、`features`（如 kline_chart）、`crosschecks`、`judge_reference`（部门级缺省，模板可覆盖）、`params_schema` |
| 流水线 CLI | `python run_pipeline.py --full --department <dept> [--stock X] [--project P] [--period T] [--spec xxx.yaml]`；部门缺省 stock_demo；judge 范文解析链：spec → profile |

## M8 将消费的产物目录（已稳定）

```
artifacts/<subject>_<ts>/
  facts.json / crosscheck_report.json / outline.json / sections.json
  reconcile_report.json / validate_report.json / judge_report.json
  revision_roundN.json / index_kline.png / final.html
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
