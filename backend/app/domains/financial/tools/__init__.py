"""financial 领域注册给 LLM 的工具（import 本包即完成注册）

文件名 == 注册的工具名，方便从工具列表直接找到实现：
- query_company.py       query_company      公司档案
- query_financials.py    query_financials   指标查询（single / trend / yoy）
- query_data_stats.py    query_data_stats   数据概览
- compare.py             compare_peers      多公司同指标对比
- dupont.py              dupont_analysis    ROE 三因子分解
- anomaly.py             detect_anomalies   三表勾稽异常

每个模块在自己文件末尾调用 harness.registry.register_tool()，
因此组合根（app/main.py）只需 import 本包。
"""
import app.domains.financial.tools.query_company       # noqa: F401
import app.domains.financial.tools.query_financials    # noqa: F401
import app.domains.financial.tools.query_data_stats    # noqa: F401
import app.domains.financial.tools.compare            # noqa: F401  compare_peers
import app.domains.financial.tools.dupont             # noqa: F401  dupont_analysis
import app.domains.financial.tools.anomaly            # noqa: F401  detect_anomalies
