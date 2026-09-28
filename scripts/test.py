import akshare as ak

df1 = ak.stock_financial_abstract(symbol="600519")
df2 = ak.stock_financial_analysis_indicator_em(symbol="600519.SH")
df3 = ak.stock_financial_analysis_indicator(symbol="600519")
sta = df1.to_string() + df2.to_string() + df3.to_string()
with open("output.txt","w",encoding="utf-8") as f:
    f.write(sta)