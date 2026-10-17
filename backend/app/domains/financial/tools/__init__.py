"""financial 领域注册给 LLM 的工具（import 本包即完成注册）

文件名 == 注册的工具名，方便从工具列表直接找到实现：
- query_company.py       query_company      公司档案
- query_financials.py    query_financials   指标查询（single / trend / yoy）
- query_data_stats.py    query_data_stats   数据概览

每个模块在自己文件末尾调用 harness.registry.register_tool()，
因此组合根（app/main.py）只需 import 本包。
"""
import app.domains.financial.tools.query_company       # noqa: F401
import app.domains.financial.tools.query_financials    # noqa: F401
import app.domains.financial.tools.query_data_stats    # noqa: F401
