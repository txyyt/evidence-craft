/* V4-09：统一用户术语层（EC.terms）——每个术语 {label, shortHelp, technical}。
   页面主文案一律用中文 label；技术键放 tooltip 帮助文本（TermHelp 组件）。
   文件与 API 数据不改名：technical 保留排障能力。
   旧字段字典（EC.fh / EC.FIELD_DICT）保留兼容：结构编辑器字段说明。 */
(function () {
  window.EC = window.EC || {};

  const T = {
    // —— 核心概念 ——
    tree: {
      label: '结构树', shortHelp: '报告的提纲：每节写什么、按什么规则写。',
      technical: 'tree / spec (Spec v2)' },
    gap_decision: {
      label: '缺口裁决', shortHelp: '决定缺数据的章节怎么办：去搜、你提供资料、定性写、还是砍掉。',
      technical: 'needs_coverage / decisions' },
    preview: {
      label: '预检', shortHelp: '生成前先跑一遍数据层，确认能取到多少事实。',
      technical: 'plan/preview → facts' },
    style_card: {
      label: '文风卡', shortHelp: '报告的语气与格式规则卡，写作时遵守。',
      technical: 'style_cards/<id>.yaml' },
    lint: {
      label: '结构检查', shortHelp: '检查结构树本身的错误与提醒（如缺数据需求、引用了不存在的表格）。',
      technical: 'trees/lint.py → errors / warnings' },
    judge: {
      label: '报告评审', shortHelp: '按五个维度给报告打分（满分 50），决定是否需要修改。',
      technical: 'judge_report.json → total / verdict' },
    reconcile: {
      label: '数字对账', shortHelp: '核对正文里的每个数字都能对上事实来源。',
      technical: 'reconcile_report.json → status' },
    validate: {
      label: '规则校验', shortHelp: '检查行文硬规则：字数区间、条数、禁用词等。',
      technical: 'validate_report.json → items' },
    // —— 评审结论 ——
    pass: { label: '已达标', shortHelp: '评审通过，可以交付。', technical: 'verdict = pass' },
    fail: { label: '需修改', shortHelp: '评审未通过，建议在反馈页签修订后重新生成。', technical: 'verdict = fail' },
    // —— judge 五维 + 结构维 ——
    structure: {
      label: '结构完整', shortHelp: '章节齐全、顺序合理、标题规范。',
      technical: 'scores.structure' },
    length: {
      label: '篇幅完整性', shortHelp: '字数是否达到各节要求的区间。',
      technical: 'scores.length / check.body_len' },
    data_support: {
      label: '数据支撑', shortHelp: '观点有没有事实和数字撑腰。',
      technical: 'scores.data_support' },
    professionalism: {
      label: '专业度', shortHelp: '行文口吻与行业表达是否专业。',
      technical: 'scores.professionalism' },
    compliance: {
      label: '合规与引用', shortHelp: '数字是否有出处、有没有违规表述。',
      technical: 'scores.compliance' },
    readability: {
      label: '可读性', shortHelp: '句子通顺、逻辑清楚、不堆砌。',
      technical: 'scores.readability' },
    risk_warning: {
      label: '风险提示', shortHelp: '不确定性与风险是否如实说明。',
      technical: 'risks section / strategy' },
    // —— 章节类型（值不变，显示中文） ——
    kind_text: { label: '综述段', shortHelp: '综合叙述的正文段落。', technical: 'kind = text' },
    kind_views: { label: '观点节', shortHelp: '多条「小标题 + 论述」的结论观点。', technical: 'kind = views' },
    kind_table: { label: '表格节', shortHelp: '代码渲染的数据表格 + 说明文字。', technical: 'kind = table' },
    kind_risk: { label: '风险节', shortHelp: '逐条列示风险与不确定性。', technical: 'kind = risk' },
    kind_figures: { label: '图件节', shortHelp: '按数据渲染的图表集合。', technical: 'kind = figures' },
    // —— 表格渲染器 ——
    renderer_facts_rows: {
      label: '事实行表格', shortHelp: '直接从事实库按行取数渲染，模型不碰数字。',
      technical: 'renderer = facts_rows' },
    renderer_generic: {
      label: '通用数据表', shortHelp: '按绑定查询结果渲染的通用表格。',
      technical: 'renderer = generic_rows / consensus_pe' },
    // —— 数据来源类型 ——
    src_rag: { label: '本地语料', shortHelp: '上传的 PDF/文档建的检索库。', technical: 'source = rag' },
    src_web: { label: '联网检索', shortHelp: '从公开网页搜索并抽取的事实。', technical: 'source = web' },
    src_db: { label: '数据库', shortHelp: '从配置的数据库查询的数据。', technical: 'source = db' },
    src_file: { label: '本地表格', shortHelp: 'Excel 等本地文件绑定出的数据。', technical: 'source = file / xlsx' },
    // —— 流程 ——
    plan: {
      label: '数据计划', shortHelp: '生成前规划的取数清单：查什么、搜什么、要什么资料。',
      technical: 'plans/<ts>.json' },
    feedback_round: {
      label: '反馈轮次', shortHelp: '一轮意见修改的记录，可回滚到修改前。',
      technical: 'rounds/<n>/ + feedback_ledger.jsonl' },
    rollback: {
      label: '回滚', shortHelp: '撤销某一轮修改，恢复当时的稿面。',
      technical: 'rollback_of / prev_sections.json' },
    model_tier: {
      label: '模型档位', shortHelp: '生成时用更快还是更高质量的模型档。',
      technical: 'model_tier' },
  };

  /** 评审 verdict → 中文（未知值原样返回）。 */
  EC.verdictLabel = (v) =>
    v === 'pass' ? '已达标' : v === 'fail' ? '需修改'
      : v === 'warn' ? '提醒' : (v == null || v === '' ? '' : String(v));

  /** 对账/校验状态 → 中文。 */
  EC.gateLabel = (s) =>
    s === 'pass' ? '通过' : s === 'fail' ? '未通过'
      : s === 'warn' ? '提醒' : (s == null || s === '' ? '—' : String(s));

  /** lint 计数 → 中文（"0 个错误 / 0 个提醒"）。 */
  EC.lintText = (lint) => {
    const e = (lint && lint.errors) || 0, w = (lint && lint.warnings) || 0;
    return `${e} 个错误 / ${w} 个提醒`;
  };

  /** 取术语（未知 key 兜底原样显示）。 */
  EC.term = (key) => T[key] || { label: key, shortHelp: '', technical: key };

  /** TermHelp：无构建小组件——中文 label + 问号 tooltip（shortHelp + 技术键）。
      用法：<term-help term="judge" /> 或 <term-help term="judge" :text="false" />（只出问号）。 */
  EC.TermHelp = {
    name: 'TermHelp',
    props: { term: { type: String, required: true },
             text: { type: Boolean, default: true } },
    computed: {
      t() { return EC.term(this.term); },
    },
    template: `
      <span class="ec-term">
        <n-tooltip trigger="hover" :style="{ maxWidth: '320px' }">
          <template #trigger>
            <span style="display:inline-flex;align-items:center;gap:2px;cursor:help">
              <span v-if="text">{{ t.label }}</span>
              <span class="ec-term-q" style="display:inline-flex;width:14px;height:14px;border-radius:50%;
                    border:1px solid currentColor;align-items:center;justify-content:center;
                    font-size:10px;opacity:.65;margin-left:2px">?</span>
            </span>
          </template>
          <div style="max-width:300px;font-size:12px;line-height:1.6">
            <b>{{ t.label }}</b>　<span style="opacity:.6">{{ t.technical }}</span><br>
            {{ t.shortHelp }}
          </div>
        </n-tooltip>
      </span>`,
  };

  // 兼容：结构编辑器旧字段字典（V4-10 高级区继续用）
  const D = {
    report_type: { label: '类型标识', desc: '报告类型的英文唯一标识（snake_case），内部与文件名使用。' },
    name: { label: '名称', desc: '界面上显示的报告类型名称。' },
    description: { label: '说明', desc: '一句话描述这类报告写什么。会写进生成 prompt，帮助模型把握整体定位。' },
    writer_role: { label: '撰写角色', desc: '生成时模型扮演的角色（如"资深行业分析师"），影响行文口吻与专业度。' },
    title_style: { label: '标题风格', desc: '报告主标题的形态要求（含范例）。模型按它拟标题，回放时也按它校验。' },
    sections: { label: '章节', desc: '报告由哪些章节组成，按顺序渲染。' },
    'section.id': { label: '章节 id', desc: '章节英文标识（snake_case），内部引用用。' },
    'section.title': { label: '章节标题', desc: '渲染在报告里的章节名。' },
    kind: { label: '章节形态', desc: 'views＝结论观点（多条小标题+论述）；table＝数据表格+说明文字；risk＝风险提示/不确定性；text＝综述段落；figures＝图件集。' },
    view_style: { label: '段落形态', desc: '本章每个观点段的写法总结（小标题形态、论证结构、语气），整段注入生成 prompt。' },
    view_slots: { label: '观点槽位', desc: '本章按固定视角逐条成文；每个槽位独立生成一条观点，互不干扰（上下文隔离）。' },
    'slot.id': { label: '槽位 id', desc: '槽位英文标识，是大纲、回放、数据兜底引用的主键，必须唯一。' },
    'slot.brief': { label: '槽位职责', desc: '这个槽位写什么、用什么数据、怎么论证。生成时模型只看这一条职责与数据切片。' },
    'slot.data_needs': { label: '数据需求', desc: '该槽位依赖的数据类别标签，用于把相关事实切片喂给模型。' },
    'slot.fewshot': { label: '范文选段', desc: '从样例里逐字摘选的示范段落，直接注入生成 prompt 供模仿——对成文质量影响最大。' },
    'check.body_len': { label: '正文字数区间', desc: '每条观点正文的字数 [下限, 上限]。超出会被规则校验打回重写。观测自样例各段字数。' },
    'check.heading_len': { label: '小标题字数区间', desc: '观点小标题的字数 [下限, 上限]，超长判 FAIL。' },
    'check.count': { label: '条数区间', desc: '风险类章节的条数 [下限, 上限]。' },
    'check.item_suffix': { label: '条目结尾词', desc: '风险每条应以什么词结尾（如"风险"）。' },
    'check.min_shaped': { label: '最少成条数', desc: '按结尾词判定"成条"的最少条数，少于它判 FAIL。' },
    strategy: { label: '风险策略', desc: 'mirror＝逐条审视前文观点、其反面即风险；enumerate＝按固定清单逐项列举。' },
    style: { label: '说明写法', desc: 'table/risk 章节说明文字的写法要求（口径注明、句式、范例）。' },
    table: { label: '关联表格', desc: '本章引用的表格模板 id（定义在 tables 列表）。表格由代码渲染，模型只写说明文字。' },
    renderer: { label: '表格渲染器', desc: 'consensus_pe＝盈利预测表（EPS/PE 由一致预期数据+公式生成，模型不碰数字）；generic_rows＝通用数据行。' },
    columns: { label: '表头列', desc: '表格的表头列名列表。' },
    writing_rules: { label: '行文规则', desc: '可从成文验证的硬性写法（数字口径、表述方式），逐条进入生成与评审。' },
    forbidden_words: { label: '禁用词', desc: '成文中不允许出现的词（第一人称、夸大措辞等），校验命中即告警。' },
    controlled_vocab: { label: '受控词表', desc: '有固定用法的词汇域（如评级词汇），模型只能从中取词。' },
    check_rules: { label: '附加校验', desc: '可配置的额外校验规则开关（如 PE 口径一致性）。' },
    judge_reference: { label: '对标范文', desc: 'judge 评审时的标杆范文文件路径。范文决定评审口味，务必用同类报告的佳作的文字版。' },
    disclaimer: { label: '免责声明', desc: '渲染在报告末尾的合规声明段。' },
    bindings: { label: '数据绑定', desc: '生成时按顺序执行的数据取数配置；上游绑定产出的值可供下游用 $ctx 引用。' },
    params_schema: { label: '生成参数', desc: '生成这份报告时需要用户填什么（如股票代码、项目编号），绑定里用 $参数名 引用。' },
  };
  EC.fh = (key) => D[key] || { label: key, desc: '（暂无说明）' };
  EC.FIELD_DICT = D;
  EC.TERMS = T;
})();
