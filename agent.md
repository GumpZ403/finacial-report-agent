# agent.md — 项目总览与协作规范

> 本文档是 `financial-report-agent` 的唯一 Agent 工作入口，包含两部分：
> **第一部分**项目架构与最终目标（项目是什么）；**第二部分**协作行为守则（怎么干活）。

---

# 第一部分 项目架构与最终目标

## 1. 项目定位

**财报解读与研究辅助 Agent**：面向 A 股上市公司财报数据（结构化数字 + 文本素材），提供
指标查询与跨公司对比、异常识别、同行画像、杜邦分析、现金流质量与财报解读报告生成的能力。

## 2. 最终目标（能力清单）

| 能力 | 说明 | 落地形态 |
|---|---|---|
| ① 指标查询与公司对比 | 自然语言 → 参数化 SQL（单公司 / 跨公司：如"宁德时代近三年毛利率趋势""宁德 vs 比亚迪 毛利率"） | `query_financials` 工具（single/trend/yoy 三模式） |
| ② 数据概览 | 公司数 / 报告期数 / 指标条数 / 行业分布 | `query_data_stats` 工具 |
| ③ 公司档案 | 全称 / 代码 / 行业 / 板块 | `query_company` 工具 |
| ④ RAG 语义检索 | 综合多期数据、跨公司比较、趋势与异常等开放性问题 | `rag_search` 工具（纯检索，生成归外层 LLM，支持多跳） |
| ⑤ 财报解读报告 | 生成解读报告（.md / .docx 输出） | plan 子图 + 写作节点 |

**RAG 定位**：`rag_search` 是**检索工具**而非独立问答管线——混合检索（向量 + BM25）+ 重排
返回片段，由对话循环的外层 LLM 组织回答；问具体数字时 LLM 应优先调 `query_financials`
（结构化 SQL 精度高于文本片段）。

**核心差异化**：指标计算全部规则化（LLM 不算数）、数据源可放量（akshare 免费）、
面向全市场 A 股的扩展性。

**合规红线（硬约束）**：不做预测、不给投资建议、不评级、不给目标价；
所有指标计算在 SQL/pandas 完成，LLM 只负责自然语言理解与表达。

## 3. 整体架构（当前生效，2026-10 自研 Flow 版）

```
D:\A_projects\finacial-report-agent\
├── backend\                        # 后端（Python，以本目录为工作目录运行）
│   ├── app\
│   │   ├── main.py                 # 组合根：FastAPI 应用 + 路由挂载 + 前端产物托管 + 工具注册
│   │   ├── interfaces\http\        # 协议适配层（只做请求/响应与 SSE 编码，不含业务）
│   │   │   ├── schemas.py          #   请求/响应 Pydantic 模型
│   │   │   └── routes\agent.py     #   /api/agent/chat · /chat/stream(SSE) · /memory · /clear
│   │   ├── harness\                # Agent 运行时（与业务无关）
│   │   │   ├── llm.py              #   LLM 客户端（OpenAI 兼容：重试 / JSON / function calling）
│   │   │   ├── state.py            #   AgentState 共享黑板（query/intent/tool_calls/messages/answer）
│   │   │   ├── registry.py         #   工具注册表（schema 给 LLM 决策，handler 给系统执行）
│   │   │   └── memory.py           #   会话记忆（短期滑窗 + 超限 LLM 摘要压缩）
│   │   ├── domains\                # 业务领域包
│   │   │   ├── conversation\       #   对话编排（单 agent 入口）
│   │   │   │   ├── loop.py         #     主循环：assemble → decide → act → answer（有界，串行工具）
│   │   │   │   └── graph.py        #     入口：build_loop() / get_agent_graph() 单例
│   │   │   ├── financial\          #   财报数据（东财三表单一数据源）
│   │   │   │   ├── models.py       #     SQLAlchemy 表模型（9 张表）
│   │   │   │   ├── db.py           #     引擎 / 会话 / 建表迁移（REQUIRED_COLUMNS 在此）
│   │   │   │   ├── repository.py   #     读写仓储（纯 CRUD）
│   │   │   │   ├── formula.py      #     公式求值（AST 白名单，纯函数，可单测）
│   │   │   │   ├── display.py      #     单位语义 + 展示形态（唯一格式化实现）
│   │   │   │   ├── fiscal_calendar.py #  期次窗口 + 披露节奏（纯函数）
│   │   │   │   ├── industry_scope.py #   行业模板 × 指标适用性（financial 模板 N/A 清单）
│   │   │   │   ├── quality.py      #     L0 质量校验
│   │   │   │   ├── corpus.py       #     结构化数据 → 检索语料片段（供 RAG）
│   │   │   │   ├── ingest\         #     外部数据入口
│   │   │   │   │   ├── eastmoney.py #      东财三表采集（akshare + 重试 + gzip 快照 + 回放）
│   │   │   │   │   └── mapping.py   #      列名映射与归一化（纯函数）
│   │   │   │   ├── services\       #     分析服务：resolver / quote / dupont / anomaly
│   │   │   │   └── tools\          #     注册给 LLM（文件名 == 工具名，共 6 个）
│   │   │   ├── retrieval\          #   检索
│   │   │   │   ├── vector.py       #     ChromaDB 向量库（financial_chunks）
│   │   │   │   ├── hybrid.py       #     混合检索（向量 + BM25，min-max 归一加权）
│   │   │   │   ├── reranker.py     #     Cross-Encoder 重排
│   │   │   │   └── tools.py        #     注册给 LLM：rag_search
│   │   │   └── report\             #   报告
│   │   │       ├── graph.py        #     Plan&Solve 报告图（当前是模板 DAG + 一次长文生成）
│   │   │       ├── agents\         #     写作子代理（多 agent 协作）——待实现，接口约定见包内 docstring
│   │   │       └── export.py       #     Markdown → docx
│   │   └── platform\               # 进程级基础设施：settings(配置/路径) / embedding(模型封装)
│   ├── scripts\                    # 个人用脚本（拉数/校验/查库）
│   ├── tests\                      # pytest
│   └── pyproject.toml              # pytest 配置
├── frontend\                       # Vue3 + Vite（唯一前端，dist/ 由 FastAPI 托管）
├── requirements.txt                # Python 依赖（唯一来源；与根目录 .venv 同级）
├── docs\                           # 方案文档（改造依据）
├── storage\                        # financial.db（财报）/ conversations.db（会话记忆）/ raw / chroma_db
├── models\                         # 本地模型缓存（bge embedding / reranker）
├── reports\                        # 报告输出目录
├── logs\                           # 日志
└── .env  agent.md  README.md
```

**请求数据流**：前端（Vue3，`frontend/`）→ `POST /api/agent/chat`（或 SSE `/chat/stream`）
→ `app/domains/conversation/loop.py`（有界循环：assemble → decide → act → answer）
→ `app/harness/registry.py` 里注册的工具（function calling）→ 返回 `{intent, agent, reply, meta}`。

**分层纪律**：数据访问只走 `domains/financial/repository.py`；外部数据源只走
`domains/financial/ingest/`；向量检索只走 `domains/retrieval/vector.py`；
Agent 不直接碰库，只通过 `domains/*/tools/` 注册到 `harness/registry.py` 的工具调用。
依赖方向只允许：`interfaces → domains → harness/platform`；`harness` 不得 import `domains`。

## 4. 数据架构

### 4.1 SQLite（storage/financial.db，SQLAlchemy 2.0，9 张表）
- **业务表（9 张）**：
  - `company_master`：公司主表（code 唯一、行业、模板）
  - `financial_periods`：报告期（(company_id, period, report_scope) 唯一）；
    `fetch_status` 记采集完整性（`ok` / `partial:cashflow` 等）——三表部分失败时该期数据不全，
    与"公司本来没这项"必须区分，报告侧据此显式标注缺失
  - `financial_metrics`：指标长表（EAV，唯一真相，(period_id, metric_code) 复合主键）；
    `updated_at` 记该值**最后一次实际变化**的时间（四项 value/unit/is_estimated/source 全没变就不写，
    历史上早于该字段的行留空 = 未知）
  - `financial_core`：宽表（一行一期次，供 SQL/对比/排行，镜像 financial_metrics）
  - `metric_dict`：指标字典（75 条，含 name_cn/unit_type/formula，语义来源）
  - `peer_groups` / `anomaly_rules`：同行组 / 异常规则（离线预计算，当前空表待用）
  - `raw_financial_payload`：原始接口响应登记，指向 `storage/raw/<code>/<时间戳>-<checksum8>.json.gz`（gzip）；
    按 (company_code, checksum) 去重，文件内容变了才产生新快照（历史版本不被覆盖）
  - `data_quality_log`：L0 质量日志
- ~~通用/配置表（3 张）：`reports` / `feedbacks` / `long_term_memories`~~ → 舆情项目遗留，**已随代码删除**，库里不存在
- 舆情域模型已全部删除。

### 4.1.1 会话记忆库（storage/conversations.db，stdlib sqlite3，2 张表）
- `sessions`：会话元信息（`title` 首问截断 / `turn_count` 服务端自增 / `summary` + `summary_upto` 摘要游标）
- `conversations`：逐轮消息（`turn_index` 单调递增，`UNIQUE(session_id, turn_index)` 做幂等护栏；`role` 有 CHECK）
- **分库理由**：财报数据是权威数据（要备份/可迁移/可重建），对话是一次性运行状态；
  且写频率差异大（每轮几行 vs 批量导入），SQLite 写串行，混库会互相等锁
- 代码：`app/harness/store.py`（`ConversationStore`，`PRAGMA user_version` 记 schema 版本）
- **现状：写已落地、读仍未切换**。每次问答都会写库，但 `get_context()` 还走进程内 `Memory`，
  所以重启后历史不会自动回到上下文；下一步把读路径切到 `load_context()` + `get_session().summary`

### 4.2 数据源与流水线（东财单源，2026-09-26 定稿）
- **接口（akshare 东财，以代码实际调用为准）**：
  - `stock_info_a_code_name`：A 股代码/名称列表（`list_companies`）
  - `stock_balance_sheet_by_report_em`：资产负债表（货币资金/存货/应付…）
  - `stock_profit_sheet_by_report_em`：利润表（营收/成本/费用明细…）
  - `stock_cash_flow_sheet_by_report_em`：现金流量表（经营/投资/筹资…）
  - `stock_individual_info_em`：公司行业 —— **尚未接入**（`industry_l1/l2/template` 现由 `scripts/fetch_slice.py`
    的 `SLICE` 常量手填，不是拉取得到的）
- **归一化**：三张映射表 `EM_BALANCE_MAP / EM_PROFIT_MAP / EM_CASHFLOW_MAP`（接口英文列 → metric_code
  中间语言，如 TOTAL_OPERATE_INCOME → revenue）；金额统一 CNY 元、比率统一小数；
  东财三表均为**累计口径**（is_single_quarter=0）
- **不再做**：新浪双源（已退役）、双源交叉验证（单次/累计口径对不上）、L0 回算死检查
- **期次窗口**：唯一来源 `Settings.PERIOD_WINDOW_YEARS`（默认 10，`.env` 可改）；
  `build_reports()` 入库裁剪与 `prune_out_of_window()` 清理都读它，不再有两处硬编码。
  判定按**日期**（`period_end ≥ 今天往前推 N 年`，见 `domains/financial/fiscal_calendar.py:years_ago`），
  不按年份字符串比大小——否则每年 1 月窗口会退化成"9 年出头"。raw 快照保存全历史，只有入库按窗口裁剪
- **采集重试**：单表最多 3 次、指数退避（1s→2s→4s）、请求间隔带 ±20% 抖动；
  永久性错误（`ImportError/AttributeError/TypeError/KeyError`，即接口签名变了）立即放弃，不做无意义重试
- **增量拉取**：若某家最新期次 == 按披露日历应已发布的期次（`fiscal_calendar.py:latest_disclosed_period`，
  一季报 4/30 / 半年报 8/31 / 三季报 10/31，1~4 月只认上年三季报），且上次采集完整、
  `FRESH_DAYS=7` 天内拉过 → 本轮跳过（`--force` 强制重拉）。
  比较用 `period_end`（`fiscal_calendar.py:period_end_of`）而不是 `period` 字符串：字母序不是时间序
  （`2026FY < 2026H1 < 2026Q1 < 2026Q3`），而且个别公司会提前披露，库里比日历更新也算 fresh
- **窗口清理**：`fetch_slice.py` 每拉完一家就对该家 `prune_out_of_window(company_code=...)`，
  只清本案公司（局部 `--limit` 运行不会误删别家），`--no-prune` 可关闭
- **采集完整性**：`FetchResult.status` 逐表记 ok/failed，任一表失败 → `partial`，
  写 `financial_periods.fetch_status`；缺失的表**不会**被当成"空科目"入库，`fetch_slice.py`
  把 partial 计入失败清单并以非 0 退出码结束
- **能力清单**：`docs/指标能力清单.md`（自动生成，`scripts/gen_capability_doc.py`）——
  逐指标列出填充率、公式与"明确不支持+原因"；不变量测试见 `tests/test_capability.py`
- **当前库（2026-10-17 真跑：akshare 1.19.1 + 东财）**：20 家公司 / 780 报告期 / 43,061 指标 /
  指标字典 75 条；raw 快照 21 份共 46.7 MB（单家 0.1~3.1 MB，最大万科 A 3.1 MB）——
  这些是**未压缩的历史快照**，新快照一律 `.json.gz`（实测 3.06 MB → 0.28 MB，全量约 4.1 MB）；
  L0：同比 100% / 单位 100% / 长表-宽表 100% / 核心指标覆盖 99.27% / 问题 0
- **重灌/续跑**：`scripts/fetch_slice.py [--limit N] [--only 代码,代码] [--force] [--no-prune]`
- **流水线**：采集（akshare）→ 原始落盘（storage/raw/）→ 归一化+映射 → 入库（SQLite）
  → L0 校验 → 文本化 → 向量化（ChromaDB）

### 4.3 向量库（ChromaDB，财报版）
- `app/domains/financial/corpus.py`：结构化财报 → 可读文本片段（overview 综合段 + anomaly 异常段 + trend 趋势段）
- `app/domains/retrieval/vector.py`：`financial_chunks` 集合，元数据 `company_code / company_name / period / group`
- **状态：1589 片段已落库**（茅台 2025Q1 营收命中 0.79、宁德趋势命中 0.78，检索实测通过）；
  embedding = bge-base-zh-v1.5（768 维），reranker = bge-reranker-v2-m3（本地缓存）
- `rag_search` 工具：混合检索（向量 + BM25 加权融合，alpha=0.5）→ Cross-Encoder 重排 → 返回片段

## 5. 里程碑进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 契约冻结：表结构 + 指标字典种子 + 派生公式 | ✅ 完成 |
| M1 | 数据层：akshare 采集 + 入库 + L0 | ✅ 完成 |
| M1.5 | 数据层收尾：schema/repository 拆分、textify、vector_db 财报版 | ✅ 完成 |
| M3 | 数据源映射：**东财三表替代新浪双源**（映射表 + 归一化 + 重拉 20 家） | ✅ 完成 |
| M4 | 工具层：query_company / query_financials（single/trend/yoy）/ query_data_stats（rag_search 已冻结，不注册） | ✅ 完成 |
| 架构 | 编排层自研 Flow + 单一有界对话循环（assemble/decide/act/answer + 标准 FC 协议） | ✅ 完成（2026-10：LangGraph→Flow；四路分叉→单循环） |
| M5 | 报告 DAG：plan 子图 + 写作 + docx 输出 | 🔄 部分（plan 子图已有，docx 落盘未接） |
| M6 | 前端财报化 | 🔄 部分（对话入口可用，可视化未做） |
| M2 | 放量全市场（**后置**：先验收全链路，再谈放量） | ⏸ 后置 |
| Redis | 会话记忆 / 检索缓存落地 | 📌 依赖已装，落地待上层细化 |

**关键路径**：M0 → M1 → M1.5 → M3 → M4 → M5 → M6；放量（M2）待全链路验收后。

## 6. 技术栈（当前生效）

- **编排层**：自研 Flow（`backend/app/harness/flow.py`；节点 + 边 + 路由器 + 子流程 + 扇出，逐节点事件与 checkpoint 钩子），单 LLM + 标准 function calling
- **存储层**：SQLite（权威数据，SQLAlchemy 2.0）+ ChromaDB（语义检索）
- **数据源**：akshare 1.18.x（东财三表 + 行业接口）
- **模型**：DeepSeek（LLM，OpenAI 兼容协议）/ bge-base-zh-v1.5（embedding）/ bge-reranker-v2-m3（重排）
- **待引入**：Redis（会话/缓存，依赖已装入 .venv）

## 7. 关键工程约束

1. **规则化计算**：指标派生/校验/异常判定全部在 SQL/pandas/Python，禁止交给 LLM 算数
2. **数据源纪律**：外部数据只走 `app/domains/financial/ingest/eastmoney.py`；映射变更后原始响应可从 `storage/raw/` 回放重算
3. **幂等入库**：所有 upsert 按业务唯一键，重复运行不产生脏数据
4. **合规**：涉及个股的回复不构成投资建议，输出标注数据来源与口径
5. **工具契约**：handler 签名统一 `(args: dict, on_stage=None) -> dict`；工具在模块加载时自动注册
   （tools_registry），main.py 显式 import 触发
6. **FC 协议**：对话循环走标准 OpenAI function calling（assistant(tool_calls) → tool(tool_call_id)），
   支持多工具并行与错误回填

---

# 第二部分 Agent 协作规范

## 一、项目现状认知

- 当前项目**架构仍有优化空间**：存在冗余模块（重复实现、职责重叠、死代码、可合并的文件与组件）。
- 冗余与重构是**持续工作**而非一次性任务。执行任何任务时保持批判性审视，发现冗余、过度设计、无用代码时主动指出，并给出收敛建议。

## 二、执行原则

1. **以用户方案为准，严格执行**
   - 严格按照用户下达的方案修改代码，不擅自偏离、不静默替换、不夹带私货。
   - 只改被点名的范围，不顺手重构无关代码；新指令与旧指令冲突时以最新指令为准。

2. **敢于提出更优方案，不盲从用户**
   - 用户下达指令后，可先提出你的想法、更优方案或风险提示，**不要一味顺着用户的思路附和**。
   - 若判断用户方案存在缺陷（架构问题、数据风险、扩展性隐患等），明确说明理由与建议，由用户拍板。
   - **提出建议 ≠ 拒绝执行**：用户未采纳你的建议时，仍严格按用户方案落地，不消极执行、不反复游说。

3. **变更最小化与可追溯**
   - 每次改动尽量小而聚焦，便于审查与回滚。
   - 不删除用户未要求删除的文件；不确定时先确认。

## 三、修改输出规范

每次代码修改，交付时必须包含**修改前后对比**：

1. **文件清单**：列出本次修改的所有文件路径。
2. **改动说明**：每个文件改了什么、为什么改（对应哪条需求/问题）。
3. **前后对比**：关键改动处给出 before / after 代码对比（diff 或对照代码块），让用户无需打开文件即可审查。
4. **影响与验证**：说明改动影响面（是否影响其他调用方），并给出已执行的验证方式与结果（编译、测试、运行等）。

## 四、工作习惯

- 全程使用中文沟通。
- 涉及数据操作（导入、删除、覆盖）前先确认范围，不改动用户未授权的内容。
- 完成任务后简洁汇报结论，避免冗长叙述。
