"""EvidenceCraft 工作台服务层（M8）。

薄层原则：只做"跑流水线 / 推事件 / 给产物 / 读写配置"，业务逻辑全部
委托给契约已冻结的 pipeline / template_factory / datalayer 模块。
"""
