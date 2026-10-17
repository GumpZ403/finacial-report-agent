# financial-report-agent

面向 A 股上市公司财报的解读与研究辅助 Agent：指标查询与跨公司对比、数据概览、
公司档案、RAG 语义检索与财报解读报告生成。

**架构**：自研轻量流程执行器（`backend/app/harness/flow.py`；单 LLM + 标准 function calling）+ SQLite + ChromaDB + akshare（东财三表）
+ FastAPI 后端 + Vue3 前端。

**合规红线**：不做预测、不给投资建议、不评级、不给目标价；所有指标计算在 SQL/pandas 完成，
LLM 只负责自然语言理解与表达。

## 目录结构

```
financial-report-agent/
├── backend/                 Python 后端（唯一后端）
│   ├── app/                 FastAPI + 自研 Flow 应用
│   ├── scripts/             数据采集 / 巡检 / 评测脚本
│   ├── tests/               pytest 基线测试
│   └── pyproject.toml       pytest 配置
├── frontend/                Vue3 + Vite 前端（唯一前端，构建产物 frontend/dist）
├── requirements.txt         Python 依赖清单（唯一来源，配合根目录 .venv）
├── storage/                 运行时数据（SQLite / Chroma / raw / cache，已 gitignore）
├── reports/                 Agent 导出报告（已 gitignore）
├── logs/                    运行日志（已 gitignore）
└── docs/                    方案文档
```

## 文档入口（按优先级）

| 文档 | 用途 |
|---|---|
| [agent.md](agent.md) | **Agent 工作入口**：项目架构、数据架构、里程碑进度、协作规范（唯一权威现状描述） |
| [docs/财报解读Agent方案_v1.1.md](docs/财报解读Agent方案_v1.1.md) | 方案正文：表结构、口径归一化、工具层、报告 DAG、评测体系 |

## 快速开始

```powershell
# 0) 依赖（Python 环境在仓库根 .venv；已存在可跳过第一行）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 1) 后端（HTTP API + 托管前端产物）
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# 或：..\.venv\Scripts\python.exe -m app.main

# 2) 前端
cd frontend
npm install
npm run build        # 产出 dist/，后端起服务后访问 http://127.0.0.1:8000
# 开发模式（热更新，前端独立端口 23617，/api 反代到 8000）：
npm run dev

# 3) 测试
cd backend
..\.venv\Scripts\python.exe -m pytest
```

## 一次对话的调用链

```
前端（Vue3）→ POST /api/agent/chat/stream（SSE）
→ graph：classify（LLM 结构化输出 intent + task_type）
→ 条件边：simple（单工具）/ react（ReAct FC 循环）/ plan（长任务）/ chat（闲聊）
→ tools：query_company / query_financials / query_data_stats / rag_search（function calling）
→ 返回 {intent, agent, reply, meta}
```

## 数据层常用命令（backend/app/domains/financial）

以下命令均以 `backend/` 为工作目录：

```powershell
..\.venv\Scripts\python.exe scripts\fetch_slice.py --limit 20   # 拉取 20 家切片（东财三表，网络）并跑 L0
#   --only 600519,000858 只跑指定公司（断点续跑 / 只重试失败）；--force 忽略增量
#   增量：最新披露期次已入库且 7 天内拉过则跳过；--force 强制重拉；--no-prune 跳过窗口清理
#   退出码：任一家失败／采集不完整 → 1（便于定时任务判断）
#   窗口：Settings.PERIOD_WINDOW_YEARS（默认 10 年，.env 可改）
..\.venv\Scripts\python.exe -m app.domains.financial.quality                 # L0 全库校验并写 data_quality_log
..\.venv\Scripts\python.exe -m scripts.eval.build_dataset                     # 生成 L3 真值集（200 条）
..\.venv\Scripts\python.exe -m scripts.eval.run_l3                            # L3 结果集比对 + 基线门禁
..\.venv\Scripts\python.exe -m scripts.eval.run_l2                            # L2 口径解析评测
..\.venv\Scripts\python.exe -m app.domains.financial.corpus --all --reindex  # 全量文本化 + 重建向量库
..\.venv\Scripts\python.exe -m app.domains.financial.corpus --dry --limit 2  # 只看文本片段质量，不入库
..\.venv\Scripts\python.exe -m app.domains.financial.ingest.eastmoney replay 300750 # 按当前映射离线回放原始响应
..\.venv\Scripts\python.exe scripts\inspect_db.py all            # SQLite / Chroma 概览
```

## 数据层口径要点（详见 agent.md 与方案）

- **唯一真相是长表** `financial_metrics`；宽表 `financial_core` 是它的镜像，不自行计算。
- **数据源**：东财三表（资产负债表/利润表/现金流量表，均累计口径）+ 映射表归一化；
  新浪双源已退役。
- **单位**由 `metric_dict.unit_type` 推导（amount→CNY 元，ratio→小数，times/days/per_share/count）。
- **向量库**：Chroma 集合 `financial_chunks`（bge-base-zh-v1.5，768 维），元数据 company/period/group；
  当前为空（见下方已知问题）。

## 当前进度与已知问题

进度以 [agent.md](agent.md) 的里程碑表为准。当前已实现：
- 数据层：东财三表采集 → 归一化 → SQLite（9 张业务表）→ 文本化 → 向量化
- 编排层：自研 Flow（`backend/app/harness/flow.py`；classify 路由 + simple/react/plan 三条执行路径 + 标准 function calling 协议）
- 工具层（6 个）：query_company / query_financials（single/trend/yoy）/ query_data_stats /
  compare_peers / dupont_analysis / detect_anomalies
- 执行层：`harness/invoke.py` 统一做参数校验 + 结构化错误 + 有界重试 + 熔断（NO_DATA ≠ 故障）
- 会话层：按 session_id 隔离的记忆（进程内），流式与非流式入口都会写
- 服务层：FastAPI（SSE 流式对话）+ Vue3 前端（唯一前端，dist 由后端托管）

**已知问题 / 待办**：
- **本地库已灌数**（2026-10-17，akshare 1.19.1 + 东财）：20 家公司 / 780 报告期 / 43,061 指标；
  L0 = 同比 100% / 单位 100% / 长表-宽表 100% / 核心指标覆盖 99.27% / 问题 0。
  `storage/chroma_db` 仍是 0 向量（文本化未跑：`python -m app.domains.financial.corpus --all --reindex`）。
- 会话记忆：**写已落 SQLite**（`storage/conversations.db`），读仍走进程内 Memory，重启后历史不会回到上下文。
- 报告阶段仍是“模板 DAG + 一次长文生成”，八章结构 / 数字白名单溯源 / 投研合规校验（方案 §6）未落地；
  写作子代理（多 agent 协作）只有接口约定（`domains/report/agents/`）。
- 评测体系（方案 §8 L0~L4）尚未落地，`scripts/eval/` 未建。
- 前端无图表（方案 §7 的 `/api/financial/series` + ECharts 未接入）。
