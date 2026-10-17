"""ingest：外部数据源适配层

- eastmoney.py  东财三表采集（DataSourceAdapter 协议 + AkshareEMAdapter + gzip 快照 + 离线回放）
- mapping.py    列名映射与归一化（纯函数，可离线单测）

纪律：外部数据只从这里进来；映射变更后可从 storage/raw/ 回放重算。
"""
