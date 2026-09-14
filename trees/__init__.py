"""结构树（Tree）工件：对话式报告工作台的模板层。

树 = Spec v2 结构 + tree: 元信息块，落 config/trees/<tree_id>/tree.yaml：
  tree.yaml            当前版（tree: 元信息 + SpecV2 字段，load_spec 忽略 tree: 块）
  versions/v<N>.yaml   每次保存的快照（对话改/手动改/回滚共用版本链）
  ops_log.jsonl        统一操作日志：actor=manual|agent|rollback|system
设计决策 D5：对话改与手动改写同一份日志、共用版本号——agent 每轮读当前树。
"""
