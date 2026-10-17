"""财务分析服务层（不注册给 LLM 的能力）

分层约定：
- `tools/`      给 LLM 的入口（解析参数 + 组织返回，不含业务规则）
- `services/`   业务能力（取数 / 口径解析 / 杜邦 / 异常），可被工具、评测、报告复用
- `repository`  纯读写；`formula` 纯算术；`display` 单位展示

当前服务：
    quote.py   取数 + 溯源字段（period / unit / display / estimated / source）
"""
