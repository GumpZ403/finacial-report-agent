"""ingest：外部数据源适配层

- akshare_em.py  东财三表采集（DataSourceAdapter 协议 + AkshareEMAdapter + 原始响应回放）

纪律：外部数据只从这里进来；映射变更后可从 storage/raw/ 回放重算。
"""
