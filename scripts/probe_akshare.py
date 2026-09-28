# -*- coding: utf-8 -*-
"""
akshare 字段探查（M1 数据层可行性验证，方案 v1.1 §3）
- 验证公司列表 / 财务摘要 / 财务指标三个接口的字段与格式
用法：.venv/Scripts/python.exe scripts/probe_akshare.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 40)


def main():
    import akshare as ak

    # 1. A 股公司代码列表
    print("=" * 60, "\n[1] 公司列表 stock_info_a_code_name")
    df = ak.stock_info_a_code_name()
    print("行数:", len(df), "| 列:", df.columns.tolist())
    print(df.head(3).to_string())

    # 2. 财务摘要（东方财富）
    print("=" * 60, "\n[2] 财务摘要 stock_financial_abstract(300750)")
    try:
        fs = ak.stock_financial_abstract(symbol="300750")
        print("列:", fs.columns.tolist())
        print(fs.head(5).to_string())
    except Exception as e:
        print("失败:", type(e).__name__, str(e)[:300])

    # 3. 财务指标（新浪源）
    print("=" * 60, "\n[3] 财务指标 stock_financial_analysis_indicator(300750, 2023)")
    try:
        fi = ak.stock_financial_analysis_indicator(symbol="300750", start_year="2023")
        print("列数:", len(fi.columns), "| 列:", fi.columns.tolist())
        print(fi.head(3).to_string())
    except Exception as e:
        print("失败:", type(e).__name__, str(e)[:300])


if __name__ == "__main__":
    main()
