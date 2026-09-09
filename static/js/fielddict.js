/* 中文字段字典：结构编辑器的每个字段是什么、对生成的影响。
   卡片 tooltip / 帮助面板 / 高级模式图例共用。 */
(function () {
  window.EC = window.EC || {};
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
})();
