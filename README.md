# financial-report-agent

面向 A 股上市公司财报的解读与研究辅助 Agent：指标查询与跨公司对比、数据概览、
公司档案、RAG 语义检索与财报解读报告生成。

**架构**：LangGraph（单 LLM + 标准 function calling）+ SQLite + ChromaDB + akshare（东财三表）。

**合规红线**：不做预测、不给投资建议、不评级、不给目标价；所有指标计算在 SQL/pandas 完成，
LLM 只负责自然语言理解与表达。

## 文档入口（按优先级）

| 文档 | 用途 |
|---|---|
| [agent.md](agent.md) | **Agent 工作入口**：项目架构、数据架构、里程碑进度、协作规范（唯一权威现状描述） |
| [docs/财报解读Agent方案_v1.1.md](docs/财报解读Agent方案_v1.1.md) | 方案正文：表结构、口径归一化、工具层、报告 DAG、评测体系 |

## 快速开始

```powershell
# 环境（Python 3.14 虚拟环境 + .env 已配置 DeepSeek API Key）
.venv\Scripts\python.exe -m app.main            # 启动 Web 对话界面（http://localhost:8000）
.venv\Scripts\python.exe -m pytest tests/ -q    # 基线测试
```

也可用 uvicorn：`.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

## 一次对话的调用链

```
前端 index.html → POST /api/agent/chat（或 SSE /chat/stream）
→ graph：classify（LLM 结构化输出 intent + task_type）
→ 条件边：simple（单工具）/ react（ReAct FC 循环）/ plan（长任务）/ chat（闲聊）
→ tools：query_company / query_financials / query_data_stats / rag_search（function calling）
→ 返回 {intent, agent, reply, meta}
```

## 数据层常用命令（app/data）

```powershell
.venv\Scripts\python.exe scripts\fetch_slice.py --limit 20  # 拉取 20 家切片（东财三表，网络）并跑 L0
.venv\Scripts\python.exe -m app.data.quality                # L0 全库校验并写 data_quality_log
.venv\Scripts\python.exe -m app.data.textify --all --reindex # 全量文本化 + 重建向量库
.venv\Scripts\python.exe -m app.data.textify --dry --limit 2 # 只看文本片段质量，不入库
.venv\Scripts\python.exe -m app.data.datasource replay 300750 # 按当前映射离线回放原始响应
.venv\Scripts\python.exe scripts\inspect_db.py all          # SQLite / Chroma 概览
```

## 数据层口径要点（详见 agent.md 与方案）

- **唯一真相是长表** `financial_metrics`；宽表 `financial_core` 是它的镜像，不自行计算。
- **数据源**：东财三表（资产负债表/利润表/现金流量表，均累计口径）+ 映射表归一化；
  新浪双源已退役。
- **单位**由 `metric_dict.unit_type` 推导（amount→CNY 元，ratio→小数，times/days/per_share/count）。
- **向量库**：1589 个财报片段（bge-base-zh-v1.5，768 维），元数据 company/period/group。

## 当前进度与已知问题

进度以 [agent.md](agent.md) 的里程碑表为准。当前已实现：
- 数据层：东财三表采集 → 归一化 → SQLite 12 表（20 家 / 833 期 / 45,964 指标）→ 文本化 → 向量化
- 编排层：LangGraph（classify 路由 + simple/react/plan 三执行路径 + 标准 function calling 协议）
- 工具层：query_company / query_financials（single/trend/yoy）/ query_data_stats / rag_search

**未完成**：报告 docx 落盘接入（M5）、前端可视化（M6）、放量全市场（M2）、Redis 落地。
