"""financial 领域注册给 LLM 的工具（import 本包即完成注册）

- company.py     query_company      公司档案
- financials.py  query_financials   指标查询（single / trend / yoy）
- stats.py       query_data_stats   数据概览

每个模块在自己文件末尾调用 harness.registry.register_tool()，
因此组合根（app/main.py）只需 import 本包。
"""
import app.domains.financial.tools.company      # noqa: F401
import app.domains.financial.tools.financials   # noqa: F401
import app.domains.financial.tools.stats        # noqa: F401
