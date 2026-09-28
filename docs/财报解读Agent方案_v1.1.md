# 财报解读与研究辅助 Agent 项目方案 v1.1（含 v1.1.1 口径修订 / v1.1.2 能力范围修订）

> v1.1 修订依据：对现有舆情 Agent 骨架（`app/router.py`、`app/agents/report_agent/workflow.py`、
> `app/tools/db_tools.py`、`app/data/vector_db.py`）的实际代码核对结果。
> 结论：骨架复用成立，但 v1.0 把难度低估在"工具层换壳"，真实难点在**数据口径层**与**评测定义**。
> 本版对这两块做了重写，其余章节小改。

> **状态标注约定（v1.1.1 新增）**：本文档描述的是**目标态设计**，通篇用陈述语气，
> 不代表已实现。实现进度以 `agent.md` 的里程碑表为准；核对代码时以 `app/` 下实际实现为准。
> 已知"文档写了、代码还没有"的部分：§5.2 全部工具、§6 报告 DAG、§7 前端接口与 SSE 事件、
> §8.4 `scripts/eval/` 评测脚本、§5.1 metric_resolver / metric_loader / anomaly_engine 服务层。

---

## 0.1 本次修订（v1.1.1：口径仲裁、L0 定义与容量参数）

| # | v1.1 的缺口 | v1.1.1 补充 | 章节 |
|---|---|---|---|
| 1 | 未规定"回算不通过时宽表用源值还是回算值"；实现用回算值覆盖宽表、又用同一份回算值校验，L0 必然 100% 通过 | 新增**口径仲裁**：源值优先、回算只校验不覆盖、派生物仅填补空缺并标 `is_estimated` | §4.3 |
| 2 | L0 只写"10~15 组恒等式、容差 1%"，未按指标分类，也未定义分母失效条件 | L0 五项检查 + 分类容差 + 分母趋零记 not_applicable + 明确通过率口径 | §8.3 |
| 3 | 未规定入库期次窗口；"1500 家 × 5 年 ≈ 3 万行"与实际的"全历史"口径差两个数量级 | 明确入库窗口与容量估算 | §3 §9 |
| 4 | `unit` 取值表写 `%`，与"比率存小数"自相矛盾 | 单位 = `metric_dict.unit_type` 映射（amount→CNY，比率→ratio） | §4.2 |
| 5 | 行业模板只给方向，未给 N/A 清单与存储方式 | 补"模板 × 指标"适用性矩阵与 N/A 约定 | §3 |
| 6 | 排期漏了 `peer_groups` / `anomaly_rules` 的生成节点（两表因此长期为空） | M0 增离线预计算、M3 增规则编写 | §9 |
| 7 | 数字白名单未定义"展示形态"；比率 ×100 后永远匹配不上白名单 | 白名单存展示形态值 | §6.4 |
| 8 | 附录 B 列 33 项却写"35 项"，且未标数据源与公式 | 附录 B 增数据源/公式/状态列 | 附录 B |

---

## 0.2 本次修订（v1.1.2：能力范围与数据基准）

| # | 变化 | 内容 | 章节 |
|---|---|---|---|
| 1 | **能力清单定稿** | 由"五层能力"改为 **6 项**：指标查询与公司对比 / 异常识别 / 同行画像 / **杜邦分析** / **现金流质量** / 财报解读报告 | §一 §二 §5.2 |
| 2 | **RAG 降级** | 不设独立问答主路径。当前数据源为纯结构化数字，textify 片段精度低于 SQL；textify + ChromaDB 保留为**报告 DAG 素材检索**与 SQL 未命中兜底 | §一 §二 §5.2 |
| 3 | **健康评分 → 同行画像** | 取消打分与评级，改为纯事实比较（本公司值 + 同行中位数/分位 + 前 5 名） | §5.3 §6.2 §10 |
| 4 | **入库期次窗口定稿** | 统一**近十年**（两源一起约束）；窗口外数据清理，原始响应留档可回放 | §3 第 7 条 |
| 5 | **数据基准刷新** | 切片实测值改为重拉后的真值：832 期 / 35,529 指标 / 字典 62 条（56 核心）/ 宽表 59 列 / 向量 1,584 片段；容量与向量化规模按十年窗口重算 | §三 §八 |
| 6 | **口径冲突显性化** | 双源交叉校验实测 **1,930 处冲突**（roe/roa/ocf_to_net_profit 等），附录 B 新增"口径待定"状态与冲突排行榜 | 附录 B |

---

## 0. 修订说明（v1.0 → v1.1）

| # | v1.0 的问题 | v1.1 对策 | 影响章节 |
|---|---|---|---|
| 1 | ~100 字段宽表直接喂 NL2SQL 自由生 SQL，schema 膨胀 + 列语义歧义（毛利率/销售毛利率/扣非毛利率） | 拆为**核心宽表（~35 列，供 SQL）+ 指标长表（EAV，供精确取值）**；新增 `metric_dict` / `metric_alias` 指标字典；LLM 只输出结构化参数，SQL 由代码模板生成 | §4 §5 |
| 2 | 期次/口径/单位未定义，"2024 毛利率"无唯一解，评测真值标不出来 | `financial_reports` 增加 `period_type` / `is_single_quarter` / `report_scope` / `unit_scale` / `audited`；入库层统一单位 | §4 附录A |
| 3 | akshare 风险只写"字段不全/限频" | 新增 `DataSourceAdapter` 抽象 + `raw_financial_payload` 原始落盘 + 幂等回放；先做 20~30 家**含金融/地产的垂直切片** | §3 |
| 4 | 一套 100 字段打全行业，金融业指标大片 NULL | 引入**行业指标模板**（通用工商 / 金融 / 其他），`company_master.industry_template` 驱动指标集 | §3 §4 |
| 5 | 同行对比的"同行"由 LLM 现编 | 新增 `peer_groups` 表，同行集合离线预计算（行业二级 + 规模分档） | §4 §5 |
| 6 | 评测测"指标提取准确率"——实际测的是 akshare，无区分度 | 评测重写为 **L0~L4 五层**，核心是口径解析、回算一致性、事实溯源、拒答率 | §8 |
| 7 | 健康评分定位模糊，易变成"看着专业但不可信" | 明确定位为**规则化打分、非评级**；公式可配置、输出原始指标+同行分位；合规上禁"评级/目标价/收益率预期" | §5 附录C |
| 8 | 前端 ECharts 数据来源未定义 | 结构化接口 `/api/financial/series` + SSE 结构化事件（`event: chart`），不解析 LLM markdown | §7 |
| 9 | 总工期 2 周 | **演示级 2 周 / 可评测版 3~4 周**；标注工作从 M0 起与开发并行 | §9 |

---

## 一、项目概述与目标

### 定位

基于现有舆情智能体架构改造的**财报解读与研究辅助 Agent**。输入公司名/股票代码，自动完成指标提取、
异常检测、同行对比与解读报告生成。定位是**投研信息处理工具**，不做预测、不给建议。

### 核心目标（2026-09-22 定稿能力清单）

1. **指标查询与公司对比**：自然语言查财务指标（"宁德时代 2024 毛利率""宁德 vs 比亚迪 谁的毛利率更高"）。
   主路径为 SQL 参数化查询（解析 → 装载 → 派生 → 结构化表格），跨公司比较复用同一引擎。
2. **异常识别**：规则引擎（anomaly_engine）检测同比骤降、现金流背离、同行离群，每条异常必须携带
   evidence（可回指的指标行）与 caveats（可能的正常解释）。
3. **同行画像**：与同行组中位数/分位做**中性事实比较**（本公司值 + 同行中位数 + 前 5 名），
   只陈述事实、**不打分、不给评级**。
4. **杜邦分析**：ROE = 净利率 × 总资产周转率 × 权益乘数，逐层拆解并定位驱动因素（公式引擎实现）。
5. **现金流质量**：净现比、利润现金含量、应收/收入匹配、经营现金流与利润背离检测（规则 + 指标）。
6. **财报解读报告**：一键生成结构化解读（概览→关键指标→异常→同行画像→风险→结论，docx 输出），
   报告模板另行重新设计（M5 前置任务）。

**RAG 定位（定稿）**：不设独立问答主路径——当前数据源为纯结构化数字，RAG 检索 textify 文本片段
精度低于 SQL。textify + ChromaDB 保留，仅作为**报告 DAG 的素材检索通道**与 SQL 未命中时的兜底。

**可量化校验**（核心竞争力，指标改写为可测量的四条）：
   - 口径解析准确率：问句 → `metric_code` + 期次 + 口径
   - 数值一致率：库内回算校验通过率（L0）+ 对人工真值的命中率（L1/L3）
   - 报告事实一致性：报告中每个数字可追溯到指标行或文本块
   - 拒答正确率：库中无数据时必须明说"没有"，不得编造

### 非目标（明确排除，防止范围蔓延）

- 不做盈利预测、不做估值、不做投资建议、不给目标价/评级/收益率预期
- 不做实时行情/盘中数据
- v1 不做全市场文本（MD&A/公告文本只覆盖 Top 300~500 家）
- 不做自动交易信号、不做组合管理

---

## 二、项目架构（沿用骨架，局部加深）

```
┌─ 前端（单文件 JS + SSE，新增 ECharts 趋势图）
│     ↓ POST /api/agent/chat（SSE 流式，含结构化事件）
│     ↓ GET  /api/financial/series（趋势图专用结构化接口）
├─ FastAPI 层（api/ + router.py 统一入口）
│     ↓ 单 Agent（意图识别 + Function Calling 循环，最多 3 轮）
├─ 工具层（沿用 TOOLS_MAP 注册表机制）
│     ├─ query_financials    指标查询与计算（主路径：单公司/跨公司对比，参数化 SQL）
│     ├─ compare_peers       同行画像（中性事实比较，读 peer_groups，不打分）
│     ├─ detect_anomalies    异常检测（规则引擎）
│     ├─ dupont_analysis     杜邦分析（ROE 拆解，公式引擎）
│     ├─ cash_flow_quality   现金流质量（净现比/利润现金含量/应收匹配）
│     ├─ query_sql           只读兜底（保留但收窄：只暴露 financial_core 视图）
│     ├─ describe_schema     表结构说明（保留，无害）
│     ├─ rag_qa              RAG 兜底（报告素材检索 + SQL 未命中兜底，强制 company/期次过滤）
│     └─ generate_report     财报解读报告 DAG（模板重新设计）
├─ 服务层（工具与 DAG 共用的内部能力，不注册给 LLM）
│     ├─ metric_resolver     中文名/别名 → metric_code 解析
│     ├─ metric_loader       参数化 SQL 取值
│     ├─ formula_engine      派生指标计算（毛利率/同比/复合增速等）
│     └─ anomaly_engine      规则引擎
├─ 数据层（分层存储）
│     ├─ SQLite：公司/报告期/指标宽表/指标长表/指标字典/同行组/异常规则
│     ├─ ChromaDB：**当前为 textify 结构化片段**（overview/anomaly/trend，1,584 条）
│     │           + 后续 MD&A/风险提示/公告/研报文本（where 过滤 company_code + period）
│     └─ BM25 缓存：关键词召回（保留现有 pickle 缓存机制）
└─ 记忆/评测（双层记忆保留；新增 scripts/eval/ 评测集与回归）
```

### 两个必须说清的实现事实

1. **WorkflowEngine 支持扇入**。`_get_ready_nodes()` 按"所有无条件前驱完成"判定，因此
   "指标装载 + 文本检索 + 异常检测 → 撰写"这种 DAG 可直接用 `add_edge` 表达，**引擎不需要改**。
2. **执行是串行的**。引擎是 `while` 循环逐个执行 ready 节点，没有并行调度。
   财报链路里 SQL 节点是毫秒级、LLM 节点是瓶颈，因此 v1 不要求并行；
   若后续要并行，改造点在 `run()` 的节点执行段，属于独立任务。

### 关键原则

- 保持**单 Agent + 工具调用**架构不变；"增加能力" = 加工具 + 加 DAG 节点 + 加服务层模块。
- **财务计算一律在 SQL/pandas，LLM 不算数**。v1.1 进一步收紧：LLM 连 SQL 都不写主路径，
  只输出结构化参数。

---

## 三、数据来源与分层

> ### 📌 v1.2 修订（2026-09-26 生效：东财单源，替代新浪双源）
>
> **数据源决策（已拍板并落地）**：
> - **唯一来源**：akshare 东财三表——`stock_balance_sheet_by_report_em`（资产负债表）、
>   `stock_profit_sheet_by_report_em`（利润表）、`stock_cash_flow_sheet_by_report_em`（现金流量表），
>   均**累计口径**（`is_single_quarter=0`）、金额单位元；行业建档用 `stock_individual_info_em`（M2 时）。
> - **新浪双源已退役**（摘要 vFD_FinanceSummary / 指标 vFD_FinancialGuideLine）：实测两源口径不一致
>   （财务指标为单次口径，与累计口径对不上），**交叉验证做不通**，已废弃。
> - **映射表保留**（EM_BALANCE_MAP / EM_PROFIT_MAP / EM_CASHFLOW_MAP）：接口英文列 → `metric_code`
>   中间语言（如 TOTAL_OPERATE_INCOME → revenue），是"字段语义命名"，与口径无关，单源也必须保留。
> - **不再做**：双源交叉校验（附录 B 冲突排行榜作废）、L0 回算死检查（19282 行全走 filled）。
> - **实测现状**：20 家 / **833** 报告期 / **45,964** 指标 / 指标字典 **75** 条 / 向量 **1589** 片段
>   （东财重拉后）；原始落盘 `storage/raw/<code>.json`，格式：
>   `{"code", "fetched_at", "params", "em_balance", "em_profit", "em_cashflow"}`（三表原始 frame）。
>
> 以下 v1.1 正文（新浪双源、双源交叉校验）**保留作历史参考，不再指导当前实现**。

| 数据 | 来源 | 存储 | 规模估算 |
|---|---|---|---|
| 财务指标（核心 33 / 扩展 ~100） | akshare 结构化接口（tushare 备选） | SQLite 宽表 + 长表 | 见下方"容量口径" |
| 管理层讨论/风险/业务文本 | 年报 PDF 解析（pdfplumber，**后置可选**） | ChromaDB | Top 300~500 家，30~60 万块向量 |
| 公告 | akshare 公告接口 | SQLite + ChromaDB | 按需 |
| 原始接口响应 | 每次拉取落盘 | raw_financial_payload 表 + storage/raw/ | 只增不改，用于回放 |

**容量口径（v1.1.2 按重拉后实测值外推，口径统一为"近十年"）**：基准样本 = 20 家 /
832 报告期（42 期/家）/ 35,529 条指标（1,776 条/家）/ `financial.db` **5.42MB**，
即约 **160 B/指标行（含索引）**、约 6.7KB/报告期。原始响应另占 `storage/raw/` **6.63MB**
（20 份 v2，含两源全部列）。

| 口径 | 公司数 | 报告期 | 指标行 | DB 体积（估） | 原始响应（估） |
|---|---|---|---|---|---|
| 当前切片（实测） | 20 | 832 | 35,529 | 5.4 MB | 6.6 MB |
| 方案目标（M2）·近十年 | 1,500 | 约 6.3 万 | 约 267 万 | 约 0.43 GB | 约 0.5 GB |
| 全 A 股（含北交所，约 5,400 家）·近十年 | 5,400 | 约 22.7 万 | 约 960 万 | 约 1.5 GB | 约 1.8 GB |

**向量化规模（重拉后实测：1,584 片段、约 79 片段/家、0.09 秒/片段、约 11 KB/片段）**：

| 口径 | 片段数（估） | 耗时（单进程 CPU） | 向量库体积（估） |
|---|---|---|---|
| 当前切片（实测） | 1,584 | 约 5 分钟（含模型加载） | 约 17 MB |
| 1,500 家 · 近十年（79 片段/家） | 约 11.9 万 | 约 3.0 小时 | 约 1.3 GB |
| 全 A 股 · 近十年 | 约 42.7 万 | 约 10.7 小时 | 约 4.7 GB |

结论：**结构化数据全量入库可行（SQLite 足够），向量化必须分层**——按十年窗口 + 只对
Top 300~500 家（或按需触发）做向量检索，否则单机 CPU 编码时间与库体积都不可接受。
这正是"先切片后放量"的必要性所在。

> 备注：`storage/chroma_db/` 当前目录 38.7MB 高于上表"约 17MB"，原因是 `--reindex`
> 清空重建后 Chroma 保留了旧 segment 文件（不自动回收）；需要精确体积时删除该目录重建即可。

### 分层原则

数字进 SQLite（精确、可校验、字段化查询）；语义文本进向量库（MD&A/风险），
**避免把结构化数字塞进 RAG**——向量库找回的数字无法保证精确，是幻觉来源。

### 拉取策略（v1.1 新增）

1. **接口层抽象**：定义 `DataSourceAdapter` 协议（`list_companies()` / `fetch_indicators(code, period)` /
   `fetch_announcements(code, since)`）。业务代码**不得直接 import akshare**，只依赖适配器。
2. **先切片后放量**：先做 20~30 家垂直切片，必须包含**银行 / 券商 / 地产 / 制造 / 消费各若干**，
   跑通映射 → 清洗 → 入库 → 回算校验全链路后再放量到 1500 家。
3. **原始响应落盘**：写入 `raw_financial_payload` 前不做任何变换。字段映射规则调整后可**离线回放重算**，
   无需重新拉取。这一条对评测尤其关键——标注真值后大概率会发现映射错了。

   落盘文件结构（v1.1.1 明确，`storage/raw/<code>.json`）：

   ```json
   {"version": 2, "code": "300750", "fetched_at": "2026-09-21 20:00:00",
    "params": {"start_year": "2021"},
    "sina_summary": {"columns": ["选项", "指标", "20251231"], "rows": [{"指标": "营业总收入", "20251231": 423700000000.0}]},
    "sina_guide":      {"columns": ["日期", "销售毛利率(%)"],      "rows": [{"日期": "2025-12-31", "销售毛利率(%)": 26.27}]}}
   ```

   同时必须向 `raw_financial_payload` **登记一行**（`payload_path` + `checksum` + `fetched_at`），
   否则"落盘可回放"没有可查的账目。回放入口：`python -m app.data.datasource replay <code>`。
   注意：v1 文件（历史遗留）存的是**归一化结果**而非原始响应，无法回放映射变更，需重拉生成 v2。
4. **入库幂等**：唯一键 `(company_id, period, report_scope)`，重复拉取执行 upsert。
5. **限频与重试**：适配器内置节流 + 指数退避；失败清单单独落盘，支持断点续拉。
6. **全量拉取是独立时间块**：多数时间在等待，但重试补漏会显著拖长，不与其他任务混排。

7. **入库期次窗口（v1.1.2 定稿：统一近十年）**：新浪摘要摘要接口返回**全历史**（实测单家公司
   41~117 个报告期，最早到 1992 年），不设限制会直接决定库体积与拉取时长。
   约定：**统一只保留最近 10 年**，两个源一起约束——
   新浪用 `start_year`（`datasource.DEFAULT_START_YEAR`，动态取"当前年 − 10"）把窗口推给上游；
   新浪摘要返回全历史，由 `build_reports()` 在本地裁掉窗口外的期次（日志记录丢弃条数）。
   库里已有的越界数据用 `repository.prune_out_of_window(10)` 清理（幂等，实测清理 848 期 / 15,904 指标）；
   窗口外的原始数据仍留在 `storage/raw/`，可用 `replay()` 回放重建。

8. **双源交叉校验与来源标注（v1.1.1 新增）**：同一指标两源都有值时，比率类优先新浪
   （列名显式声明口径）、金额类优先新浪摘要；偏差超容差（比率 1 个百分点 / 金额 100 万元）记为
   `conflicts` 并告警，不静默丢弃。每条指标在 `financial_metrics.source` 记录取值来源
   （sina_summary / sina / derived / manual）。

### 行业模板（v1.1 新增）

`company_master.industry_template ∈ {general, financial, other}`，驱动该公司的指标集：

- `general`：毛利率、存货周转、应收周转等正常适用
- `financial`：利息净收入、不良率、拨备覆盖率、净息差等；毛利率/存货类指标置为 N/A
- `other`：兜底，仅计算通用项

避免一套 100 字段打全行业产生的空洞污染同行对比与同行画像。

**模板 × 指标适用性矩阵（v1.1.1 补充）**——下表为 `financial` 模板下明确**不适用**的指标，
其余核心指标照常适用。清单在代码中的唯一出处是 `app/data/metric_scope.py`：

| 不适用的指标 | 原因 |
|---|---|
| `gross_margin`、`gross_profit`、`operate_cost` | 金融业无"营业成本/毛利率"口径，新浪摘要给出的近似值会误导阅读 |
| `inventory`、`inventory_turnover`、`inventory_days` | 无存货概念 |
| `receivable`、`receivable_turnover`、`receivable_days` | 应收账款口径不可比 |
| `payable_turnover`、`payable_days`、`cash_conversion_cycle` | 应付账款与现金转换周期对金融业无意义 |
| `current_ratio`、`quick_ratio`、`quick_ratio_conservative`、`cash_ratio` | 银行/保险不披露流动性比率（实测金融模板下覆盖率 0%） |
| `current_asset_turnover`、`fixed_asset_turnover` | 银行业无"流动资产/固定资产周转"口径 |
| `roic` | 银行业投入资本口径不适用 |

> 上表前四行已在代码中落地（`app/data/metric_scope.py`）；**后三行待纳入**（v1.1.2 待办）。
> 未纳入前，这些"金融业天然没有"的指标会被计为"数据缺失"，把核心指标覆盖率压低
> （当前 76.97% 中含相当比例的此类假缺失）。

**N/A 的存储方式（v1.1.1 明确）**：不适用的指标**照常入库**（保留可审计的原始数据，不丢信息），
但消费方（文本化 / 质检 / 同行对比 / 画像）**必须**过滤后再用；完整性统计的分母也要排除这些指标。

**数据源口径结论：新浪摘要"营业成本" = 营业总成本（2026-09-22 定案，已修复并重拉）**

- 实测证据：茅台 2026H1，新浪摘要该行 **309.46 亿**，而同分区「毛利率」**89.56%**（真实营业成本
  应为 96.38 亿）→ 该行是总成本口径，与新浪摘要自己的毛利率互相矛盾，不可当营业成本使用。
- 修复：该行改挂 `total_operating_cost`（原始数据保留）；`operate_cost` 改由新浪
  「主营业务成本率(%)」派生（`revenue * operate_cost_ratio`）；`gross_profit = revenue - operate_cost`。
- 验证：重拉后茅台 2026H1 三值自洽（营收 922.78 亿 / 营业成本 96.38 亿 / 毛利率 89.56%），
  定案前"1409 期毛利率回算不成立"的问题消失（L0 回算 96.77%）。
- **副作用（必须记住）**：`operate_cost` 与 `gross_margin` 现在同源于新浪，二者自洽属于
  "同源内部一致"，**跨源交叉验证的职责已转移到 conflicts 告警**（见第 8 条与附录 B 冲突表）。

---

## 四、数据模型（v1.1 重写，本节为核心）

### 4.1 设计决策：宽表 vs 长表

**问题**：v1.0 用单张 ~100 列 financial_indicators，NL2SQL 面对 100 列宽表选列，准确率不可控。

**v1.1 方案：宽长双存，各司其职。**

| | 核心宽表 financial_core | 指标长表 financial_metrics |
|---|---|---|
| 列 | ~35 个高频指标（一行一报告期） | (report_id, metric_code, value, unit, is_estimated) |
| 用途 | 同行对比、排行、跨公司 SQL、NL2SQL 兜底 | 精确取单值、扩展指标、指标字典驱动 |
| 写入 | 由长表 ETL 派生，可重算 | 入库主路径 |
| NL2SQL 可见性 | 允许 | 允许（按 metric_code 过滤） |
| 全量 100+ 列 | 不暴露给 LLM | 以 metric_code 维度暴露 |

这样 NL2SQL 只面对窄表 + 单维度过滤，列歧义问题基本消失；同时保留 SQL 的灵活度。

### 4.2 表结构

```sql
-- 公司主表
company_master(
  company_id        INTEGER PK,
  code              TEXT UNIQUE,   -- 统一 6 位代码，不含后缀，如 300750（展示层按交易所补 .SH/.SZ/.BJ）
  name              TEXT,
  name_aliases      TEXT,          -- JSON: ["宁德时代","CATL","宁王"]
  industry_l1       TEXT,
  industry_l2       TEXT,
  industry_template TEXT,          -- general / financial / other
  exchange          TEXT,
  listing_date      TEXT,
  currency          TEXT DEFAULT 'CNY',
  status            TEXT DEFAULT 'active'
)

-- 报告期表
financial_reports(
  report_id         INTEGER PK,
  company_id        INTEGER FK,
  period            TEXT,          -- '2024Q3' 归一化期次标识
  period_end        TEXT,          -- 报告期末日期
  period_type       TEXT,          -- FY / H1 / Q1 / Q3
  is_single_quarter INTEGER,       -- 0=累计口径, 1=单季口径
  report_scope      TEXT,          -- consolidated / parent
  unit_scale        TEXT,          -- 归一化后统一为 CNY（元）
  audited           INTEGER,
  source            TEXT,          -- akshare / tushare / manual
  source_version    TEXT,
  raw_payload_id    INTEGER,
  fetched_at        TEXT,
  UNIQUE(company_id, period, report_scope)
)

-- 指标长表（主存储，全库唯一真相）
financial_metrics(
  report_id   INTEGER FK,
  metric_code TEXT,                -- 关联 metric_dict.metric_code
  value       REAL,
  unit        TEXT,                -- 由 metric_dict.unit_type 推导：amount→CNY，其余 ratio/times/days/per_share/count
                                   --（比率在库内是小数，故标 ratio 而非 %，避免"值是 0.2531 却标百分号"的矛盾）
  is_estimated INTEGER DEFAULT 0,  -- 1 = 非源数据（公式派生填空）或回算校验未通过
  source      TEXT,                -- sina_summary / sina / derived / manual；空 = 历史遗留未标注
  PRIMARY KEY(report_id, metric_code)
)
-- 索引：ix_metrics_metric_code(metric_code)、ix_metrics_source(source)

-- 核心宽表（长表的派生**镜像**，供 SQL 与人读）
-- 约束：宽表只能镜像长表已存在的值，缺值为 NULL，不在此处计算（L0 core_mirror 逐列核对）
-- 规模：33 个指标列（附录 B 的 33 项核心指标）+ report_id / company_id / period 三个键列
financial_core(
  report_id     INTEGER PK,
  company_id    INTEGER,
  period        TEXT,
  revenue, revenue_yoy, net_profit, net_profit_yoy,
  gross_profit, gross_margin, net_margin, roe, roa,
  operating_cash_flow, ocf_to_net_profit,
  total_assets, total_liabilities, total_equity,
  debt_ratio, current_ratio, quick_ratio,
  inventory, inventory_turnover, receivable, receivable_turnover,
  eps, bps, ...
  -- v1.1.1 新增（原只存在于长表，进不了文本与对比）：
  net_profit_parent, roe_weighted, inventory_days, receivable_days, payable_days
)

-- 指标字典（语义来源，口径解析的依据）
metric_dict(
  metric_code    TEXT PK,          -- 'gross_margin'
  name_cn        TEXT,             -- '毛利率'
  aliases        TEXT,             -- JSON: ["销售毛利率","毛利率(销售)"]
  unit_type      TEXT,             -- ratio / amount / days / per_share
  value_scale    REAL DEFAULT 1,   -- 展示换算因子
  direction      TEXT,             -- positive / negative / neutral
  formula        TEXT,             -- 派生公式（可执行表达式）
  industry_scope TEXT,             -- general / financial / other
  description    TEXT,
  deprecated     INTEGER DEFAULT 0
)

-- 指标别名（解析检索用，单独建表便于索引与模糊匹配）
metric_alias(
  alias       TEXT,
  metric_code TEXT,
  weight      REAL DEFAULT 1.0,
  PRIMARY KEY(alias, metric_code)
)

-- 同行组（离线预计算，不由 LLM 现编）
peer_groups(
  group_id   INTEGER PK,
  company_id INTEGER,
  group_name TEXT,                 -- '电池-千亿以上'
  basis      TEXT,                 -- 'industry_l2 + revenue_decile'
  updated_at TEXT
)

-- 异常规则（阈值可配，规则化）
anomaly_rules(
  rule_id        TEXT PK,
  name           TEXT,             -- '毛利率同比骤降'
  metric_code    TEXT,
  compare        TEXT,             -- yoy / qoq / vs_peer_median
  operator       TEXT,             -- lt / gt / abs_gt
  threshold      REAL,
  severity_map   TEXT,             -- JSON: {"1.5":"high","0.8":"medium"}
  industry_scope TEXT,             -- general / financial / other / all
  seasonality    TEXT,             -- 季节性调整说明
  enabled        INTEGER DEFAULT 1
)

-- 原始接口响应（只增不改，用于回放）
raw_financial_payload(
  payload_id   INTEGER PK,
  company_id   INTEGER,            -- 落盘时公司可能尚未建档，允许为空
  company_code TEXT,               -- 统一 6 位代码（建档前也能回放）
  source       TEXT,
  api_name     TEXT,
  params       TEXT,               -- JSON
  payload_path TEXT,               -- storage/raw/ 下文件路径（v2 结构，见 §3）
  checksum     TEXT,
  fetched_at   TEXT
)

-- 数据质量日志（L0 评测的数据源）
data_quality_log(
  id          INTEGER PK,
  report_id   INTEGER,
  metric_code TEXT,
  check_name  TEXT,                -- recompute_mismatch / unit_inconsistent / yoy_break
  level       TEXT,                -- error / warn / info（info = 每轮运行的汇总行）
  detail      TEXT,
  status      TEXT,                -- open / fixed / accepted
  checked_at  TEXT
)
```

### 4.3 单位与口径归一化（硬要求）

1. **金额一律归一为"元"**（`unit_scale='CNY'`），展示层除以 1e4/1e8 显示"万元/亿元"。
   混用单位是"AI 算错数"的最常见假象——实际是数据本身就是错的。
2. **比率统一存小数**（毛利率 0.2531 而非 25.31），`unit_type='ratio'`。
3. **期次必须区分累计与单季**：`period + is_single_quarter` 联合唯一确定一个数。
   问句里的"2024 毛利率"默认取 **FY 累计**，回答中必须显式声明所取口径。
4. **合并/母公司口径**在库中区分存储，默认用 `consolidated`。
5. **期次窗口（v1.1.2 与 §3 第 7 条对齐）**：统一**近十年**——新浪用 `start_year` 约束、
   新浪摘要由 `build_reports()` 本地裁剪；**窗口外的行直接清理**（`repository.prune_out_of_window`），
   原始响应留档在 `storage/raw/`，可用 `replay()` 重建。理由：库体积与向量化规模都由窗口决定，
   "保留并标记"会让全量入库成本随历史无限增长。
6. **单位来源唯一**：`unit` 一律由 `metric_dict.unit_type` 推导（amount→CNY，其余原样），
   禁止调用方传单位值，禁止按数值大小猜单位。
7. **口径仲裁（v1.1.1 新增，本节最关键的一条）**——回算与源值冲突时：

   | 情况 | 处理 |
   |---|---|
   | 指标有源值（`source ∈ {sina_summary, sina_guide, manual}`） | **源值入库并保留**；回算结果**只用于校验**，不覆盖 |
   | 指标无源值但有公式 | 由 `metric_dict.formula` 派生填空，记 `source='derived'`、`is_estimated=1` |
   | 回算与源值不一致 | 写 `data_quality_log`（`recompute_mismatch`，level=error）并标记 `is_estimated=1`；**不改写源值** |
   | 宽表 `financial_core` | 逐列镜像长表，缺值为 NULL；不自行计算、不引入长表没有的值 |

   理由：若允许回算覆盖宽表、又用同一份回算值做校验，L0 会"自证"通过（这正是 v1.1 实现里
   `net_margin` 有 1567 期长表/宽表不一致、而 L0 仍报 100% 的原因）。源值为准才能让 L0 成为
   对数据源的真检验。

---

## 五、工具层与服务层设计

统一签名沿用现有约定 `(args: dict | None, on_stage=None) -> {intent, agent, reply, meta}`，
由 `_normalize_result` 兜底校验。

### 5.1 服务层（不注册给 LLM，供工具与 DAG 复用）

**`metric_resolver`：中文名/别名 → metric_code**

```
resolve(question_metric: str) -> {
  metric_code: str | None,
  confidence: float,
  candidates: [{metric_code, score}],   # 低置信时返回多候选，由 LLM 澄清
  matched_alias: str
}
```

实现：`metric_alias` 精确匹配 → 别名表 BM25/向量召回 → 阈值判定。
置信度低于阈值时**必须反问澄清**，不得猜（这是拒答机制的一部分）。

**`metric_loader`：参数化取值**

```
load(company_ids: list[int], metric_codes: list[str],
     periods: list[str], period_type: str, is_single_quarter: int,
     report_scope: str) -> DataFrame[company_id, period, metric_code, value, unit]
```

内部拼参数化 SQL（表名/列名走白名单，值为绑定参数），**不接受 LLM 生成的 SQL 片段**。

**`formula_engine`：派生指标**

输入 `metric_dict.formula` 表达式（如 `1 - operate_cost / revenue`），在 pandas 上求值，
输出结果同时记录依赖的原始指标行，供溯源。

**`anomaly_engine`：规则引擎**

读 `anomaly_rules`，逐条规则在 `financial_core` / `financial_metrics` 上执行，输出统一结构：

```json
{
  "rule_id": "gross_margin_drop",
  "severity": "high",
  "metric_code": "gross_margin",
  "period": "2024Q3",
  "observed": 0.1211,
  "baseline": 0.2531,
  "delta": -0.1320,
  "compare": "yoy",
  "evidence": [{"report_id": 10231, "metric_code": "gross_margin"}],
  "caveats": ["Q4 应收季节性偏高，本规则已按季调整", "存在并购导致并表变更的可能"],
  "industry_adjusted": true
}
```

**硬要求**：每条异常必须携带 `evidence`（可回指的指标行）与 `caveats`（可能的正常解释）。
没有 evidence 的异常不得进入报告——这是防"AI 编异常"的机制落点。

**`dupont`：杜邦分解（v1.1.2 新增）**

- 定义：`ROE = 净利率 × 总资产周转率 × 权益乘数`
- **必须先重算因子再相乘**，不得直接取库内三个指标相乘。实测直接相乘与库内 `roe` 的中位偏差
  **2.6 个百分点**（200 个 FY 样本中仅 **19%** 落在 1pp 内，5% 超过 10pp；例：招商银行
  14.12% vs 11.80%、宁德时代 26.29% vs 21.42%、万科A −57.5% vs −75.8%）。原因是因子与 `roe`
  来自不同源或不同算法（净利率=新浪净利润口径、总资产周转率=新浪摘要平均资产口径、
  权益乘数=新浪摘要期末口径、`roe` 可能为加权口径）
- 因子口径（同源同算法，全部基于库内 `revenue / net_profit / total_assets / total_equity`）：

  | 因子 | 公式 | 说明 |
  |---|---|---|
  | 净利率 | `net_profit / revenue` | 与 `net_margin` 同口径（含少数股东） |
  | 总资产周转率 | `revenue / total_assets` | **期末**总资产（非平均） |
  | 权益乘数 | `total_assets / total_equity` | **期末** |

- 输出必须声明："以上因子由期末数按固定公式计算，与报表披露的 ROE 口径不同，乘积与报表 ROE
  可能存在差异"；若差异 >1 个百分点，报告中必须显式标注该差异
- **合规**：只做拆解与事实呈现，**禁止输出"改善空间 / 提升建议 / 应优化哪一项"**（隐性投资建议）

**`cash_flow_quality`：现金流质量（v1.1.2 新增）**

| 子项 | 公式 / 口径 | 数据可用性 |
|---|---|---|
| 净现比 | `operating_cash_flow / net_profit` | 已采集；分母趋零或为负 → 标注"不适用" |
| 利润现金含量 | `ocf_to_revenue`（经营现金流 / 营业收入） | 已采集。**不得再用 OCF/净利润**，那与净现比同值（避免同一报告里重复呈现同一指标） |
| 应收匹配 | 改用 `receivable_days`（应收账款周转天数）趋势替代 | **应收账款余额该源不提供**（实测 0 行），原"应收/收入匹配"无法计算 |
| 现金流背离 | `ocf_to_net_profit` 连续两期方向相反且幅度超阈值 | 已采集（走 `anomaly_engine`） |

- **合规**：只陈述"净现比低于同行中位数 X%"这类事实，**禁止写"财务造假嫌疑 / 利润粉饰 /
  盈余管理"** 等结论性判断

### 5.2 注册给 LLM 的工具

| 工具 | 输入参数 | 说明 |
|---|---|---|
| `query_financials` | `company`(可多个), `metric`, `period`, `period_type`, `compare`(yoy/qoq/none) | 主路径：解析 → 参数化装载 → 派生计算 → 结构化表格 + 口径声明；支持跨公司比较 |
| `compare_peers` | `company`, `metric`, `period`, `peer_group`(可选) | 读 `peer_groups`，输出本公司值 + 同行中位数/分位 + 前 5 名（中性画像，不打分） |
| `detect_anomalies` | `company`, `period`(可选，默认近 8 期) | 调 `anomaly_engine`，返回异常列表 |
| `dupont_analysis` | `company`, `period`(默认最近 FY) | 杜邦拆解：ROE → 净利率 × 周转 × 权益乘数；**因子按期末同源重算**并声明与报表 ROE 的口径差异 |
| `cash_flow_quality` | `company`, `period`(可选) | 净现比（OCF/净利润）+ 利润现金含量（`ocf_to_revenue`）+ 应收周转天数趋势（余额该源不提供）+ 现金流背离 |
| `rag_qa` | `query` | 兜底：SQL 未命中时检索文本片段，强制 company_id/期次 `where` 过滤；返回 sources |
| `query_sql` | `question` | **收窄**：schema 提示只给 `financial_core` 与 `financial_metrics` 两张视图，不给其他表 |
| `describe_schema` | — | 保留 |
| `generate_report` | `company`, `period`, `template` | 触发 DAG（§6），模板按定稿结构重新设计 |

> **为什么 `query_sql` 要收窄**：保留它作为兜底很方便，但若把 100 列宽表暴露给它，
> LLM 会绕过口径层直接选列，等于把 §4 的防护全部作废。收窄到两张视图是低成本高收益的约束。

### 5.3 同行画像（定位约束，2026-09-22 定稿替代原"健康评分"）

- 输出形态：本公司各指标值 + 同行组中位数/分位 + 前 5 名，**纯事实陈述**
- 不产分数、不产评级、不产"优于/劣于"的结论性评价，只呈现"高于/低于中位数 X 个百分点"的事实
- 同行组由离线预计算的 `peer_groups` 提供（industry_l2 + 规模分位），不由 LLM 现编
- 参与画像的指标走 `metric_scope.is_applicable` 过滤（金融模板下经营性指标排除）
- 文案固定声明："以上为基于公开财务数据的同行对比事实，不代表投资评级，不构成投资建议。"

---

## 六、财报解读报告 DAG（改造）

### 6.1 节点编排

```
口径解析 ──┬─→ 指标装载（SQL） ──────────┐
           ├─→ 文本检索（Chroma，素材）──┼─→ 异常检测 ──→ 同行画像 ──→ 撰写 ──→ 投研合规 ──→ docx
           ├─→ 同行对比 ────────────────┤
           └─→ 杜邦/现金流质量（公式引擎）─┘
```

- 输入：`company` + `period`（默认最近 FY）
- 复用现有 `WorkflowEngine`，无需改引擎；`口径解析` 作为扇出起点，`异常检测` 为扇入点
- 合规不通过的"修正→重校验"闭环沿用现有 `ReviewerAgent` 内部循环（最多 2 轮）

### 6.2 报告结构

1. 公司概览（基本信息、行业、本期/上期核心数据一句话结论，含口径声明）
2. 关键指标表（含同比，标注单位与口径）
3. 指标趋势图数据（同时推给前端 ECharts）
4. 异常与变化（每条规则给 observed / baseline / delta / evidence / caveats）
5. 同行画像（本公司 vs 同行中位数与分位，纯事实陈述）
6. 风险提示（来自文本检索的原文片段 + 出处）
7. 小结（**仅陈述已展示事实**，不含预测与建议）
8. 数据来源与口径附录（数据源、拉取时间、指标口径、映射/公式版本号）

### 6.3 强制溯源机制（可自动化校验的实现方式）

不能只靠提示词要求"有来源"，要造成**结构性约束**：

1. 撰写节点的输入是前序节点产出的**数字白名单**（report_id + metric_code + value 表格）
   与**文本证据白名单**（chunk_id + 原文 + 出处）。
2. 撰写节点被要求在引用数字时带上 `{{m:report_id:metric_code}}` 形式的锚点（或等价结构化引用）。
3. 合规节点用脚本从成稿中抽取**全部阿拉伯数字**，逐个检查是否存在于白名单；
   只要出现白名单外的数字即判不通过（阈值/期次等元信息除外，使用单独白名单）。

   **白名单必须是"展示形态"的值（v1.1.1 补充）**：库内比率是小数、报告中是 ×100 的百分数，
   库内金额是元、报告中是亿元。若白名单直接存库内值，报告里的 `25.31` 永远匹配不上 `0.2531`，
   校验会全线失败。约定：白名单由"库内值 + 展示规则"渲染后生成（同一条指标按 `unit_type`
   渲染出 `%`/`倍`/`次`/`天`/`亿元`/`元/股` 等全部合法形态），比对时先对抽取到的数字做
   单位归一，再做集合匹配；渲染规则与指标的对应关系以 `metric_dict.unit_type` 为准。
4. 文本类断言必须能回指到 chunk_id，否则删除或改写。
5. 校验结果写入报告元数据，供 L4 评测直接读取（§8）。

### 6.4 投研合规规则集（替换原舆情合规）

| 规则 | 判定方式 |
|---|---|
| 禁预测性表述 | 关键词/句式：预测、预计、将上涨、有望、目标价、未来 N 年 |
| 禁投资建议 | 买入、增持、减持、推荐、建议持有、值得配置 |
| 禁评级 | 评级、上调/下调评级、A 级/B 级等自造评级 |
| 禁收益率预期 | 预期收益、上涨空间、翻倍、跑赢 |
| 数字必须可溯源 | §6.3 白名单校验 |
| 口径必须声明 | 每个指标展示处标注期次 + 累计/单季 + 合并/母公司 + 单位 |
| 数据缺失必须显式标注 | 无数据处写"数据缺失"，不得留空或估算填充 |

该规则集独立于提示词，用可执行脚本实现，可单独跑回归（prompt 变动不影响判定）。

---

## 七、前端

### 接口

| 接口 | 用途 |
|---|---|
| `POST /api/agent/chat`（SSE） | 对话主入口，流式 |
| `GET /api/financial/series?company=&metrics=&periods=` | ECharts 趋势图**专用结构化数据源** |
| `GET /api/financial/compare?company=&metric=&period=` | 同行对比图 |
| `GET /api/report/{id}/docx` | 报告下载 |

### SSE 事件类型（v1.1 明确）

沿用现有 stage / result / error，新增：

```
event: chart
data: {"chart_id":"margin_trend","type":"line","x":["2022FY",...],
       "series":[{"name":"毛利率","unit":"%","data":[...]}]}
```

**硬要求**：前端不得解析 LLM 回复中的 markdown 表格来画图。
趋势图数据一律来自结构化事件或 `/api/financial/series`。

### ECharts 组件

- 趋势图（多指标多期，双 Y 轴：金额 / 比率）
- 同行对比（本公司 vs 中位数 vs 分位区间的横向条形图）
- 结构图（成本/费用占比，可选）

---

## 八、评测体系（v1.1 重写，核心竞争力所在）

### 8.1 第一原则：分清"测数据源"和"测系统"

指标来自结构化接口时，"指标提取准确率"测的是 akshare 的准确率，没有区分度。
v1.1 只把**系统自身的判断**作为评测对象（口径解析、计算、溯源、拒答），
数据源质量单独作为 L0 监控，不混入系统得分。

### 8.2 五层评测

| 层 | 名称 | 对象 | 是否自动 | 规模 |
|---|---|---|---|---|
| L0 | 数据质量 | 库内自洽性 | 全自动 | 全库 3.6 万行（当前 35,529） |
| L1 | 指标真值 | 人工从年报 PDF 核对 | 人工标注 | 50~100 家（分层抽样） |
| L2 | 口径解析 | 问句 → metric_code + 期次 + 口径 | 可半自动 | 300~500 条 |
| L3 | 查询计算正确率 | 结果集比对（**不是 SQL 文本比对**） | 自动 | 由 L1/L2 派生 |
| L4 | 报告质量 | 事实一致性 + 合规 + 拒答 | 自动 | 每份报告 |

### 8.3 各层细则

**L0 数据质量（全自动，每次入库/每日跑）**

v1.1.1 把 L0 从"一句原则"细化为**五项可执行检查**（实现：`app/data/quality.py`，唯一实现；
`scripts/l0_validate.py` 只是它的命令行外壳）：

| # | 检查 | 判定 | 容差 / 失效条件 | 级别 |
|---|---|---|---|---|
| 1 | `recompute_mismatch` 回算一致性 | 按 `metric_dict.formula` 反算**源值**并比对 | 比率：1 个百分点；金额：1%（不低于 100 万元）；次数/天数/每股：1% | error |
| 2 | `yoy_not_reproducible` 同比可复现 | `revenue_yoy` / `net_profit_yoy` 与上年同期值复算一致 | 1 个百分点；**上年同期 ≤ 0 或无值 → not_applicable（不算失败）** | warn |
| 3 | `unit_inconsistent` 单位一致性 | 同一 `metric_code` 全库单位唯一，且等于 `metric_dict.unit_type` 推导值 | 严格相等；字典缺该指标 → `metric_dict_gap` | error / warn |
| 4 | `core_mirror_mismatch` 长表-宽表一致 | 宽表每一列必须等于长表同指标值 | 1e-6（浮点） | error |
| 5 | `coverage_zero` / `coverage_low` 完整性 | 核心指标非空率，**按行业模板分别统计且排除模板 N/A 指标** | 0% → warn；< 50% → warn | warn |

**分母失效与"不适用"（v1.1.1 新增）**：`net_margin`、`gross_margin`、`debt_ratio` 的分母为
营收/总资产，`ocf_to_net_profit` 的分母为净利润。分母缺失、为负或绝对值小于营收的 2% 时，
该期**记 not_applicable（skip）**，既不判通过也不判失败——净现比在净利润趋零时可达上百倍，
按"不通过"处理只会制造噪音。

**防自证规则（v1.1.2 新增）**：若被校验指标的公式**依赖项**里存在派生值（`source='derived'`），
该次回算记 `skip`（依赖为派生值 → 不可用于交叉验证）。例：`total_liabilities` 若由
`total_assets * debt_ratio` 派生，则 `debt_ratio ≈ total_liabilities / total_assets`
会退化成循环校验，必须跳过——这正是"回算只校验不覆盖"（§4.3 第 7 条）在实现层的必要补充。

**通过率口径（v1.1.1 明确）**：

- `通过率 = pass / (pass + fail)`，**不含 skip 与 derived-填充**；
- DDL 变更/报表中必须写清是"哪一项检查的通过率 / 样本口径是哪一层"，不得用单一数字代表"L0 通过率"；
- **禁止抽样**：抽样结果不得称为通过率（v1.1 实现只校验每家公司最后一个报告期，却记为"L0 100%"）；
- 每轮运行写一条 `check_name='l0_run'`、`level='info'` 的汇总行，使通过率本身可审计；
- 运行方式：`python -m app.data.quality`（`--no-mark` 不标记 `is_estimated`，`--keep-log` 保留历史日志）。

**L1 指标真值（人工，工作量最大，必须与开发并行）**

- 分层抽样 50~100 家：行业配额（金融 ≥ 10%、地产 ≥ 10%、制造/消费为主）、规模配额（大/中/小）
- 每家核 10~15 个关键指标 × 近 3 年 FY，逐项从年报 PDF 抄真值
- 单家 30~60 分钟 → **总计约 40~90 人时，这是独立的人力密集型任务**
- 产出：`scripts/eval/datasets/ground_truth.csv`

**L2 口径解析（半自动）**

- 用模板生成问句变体（"毛利率" / "销售毛利率" / "2024年毛利率" / "2024Q3 单季毛利率" / "宁王去年毛利率"）
- 标注目标 metric_code + period + period_type + is_single_quarter
- 指标：Top-1 准确率、Top-3 召回率、**低置信时是否正确触发澄清/拒答**

**L3 查询计算正确率**

- 比对**结果集**（值 + 期次 + 单位），不比对 SQL 文本
- 覆盖：单值查询、同比、多期序列、同行对比、派生指标
- 派生指标需验证依赖链溯源正确

**L4 报告质量（全自动，每份报告跑）**

- 事实一致性：数字白名单命中率、无来源数字数（要求 0）
- 合规：§6.4 规则集全部通过
- 拒答：构造"库中无此公司/无此期次/无此指标"的问题，检查是否明确说明缺失而非编造
- 结构完整性：8 个章节齐全、口径声明齐全

### 8.4 回归机制

```
scripts/eval/
├── run_all.py            # 一键跑 L0~L4，输出 eval_report.md + eval_report.json
├── l0_data_quality.py
├── l1_ground_truth.py
├── l2_metric_parsing.py
├── l3_query_accuracy.py
├── l4_report_quality.py
├── datasets/
│   ├── ground_truth.csv
│   └── queries.jsonl
└── baselines/
    └── baseline_v1.json   # 各指标基线阈值
```

- 触发时机：每次改 prompt / 工具 / 指标映射 / 同行分组 / 异常规则后必跑
- 门禁：任一指标低于基线即视为回归，阻止合并（对应 agent.md 的最小改动可追溯原则）
- 报告格式：分指标展示 当前值 / 基线 / 变化，便于快速定位退化点

---

## 九、里程碑（v1.1 修订排期）

单人开发估算：**演示级 2 周 / 可评测版 3~4 周**。

| 阶段 | 内容 | 工期 | 完成判据（DoD） |
|---|---|---|---|
| **M0 契约冻结** | 表结构、metric_dict 首批 33 指标、工具入参出参签名、评测层定义全部定稿；**同时启动 L1 人工标注**；<br>**（v1.1.1 新增）离线预计算 `peer_groups`、编写 `anomaly_rules` 初版规则** —— 二者纯离线、不依赖 LLM，是 §5.2 三个工具的前置 | 0.5~1 天（预计算 +0.5 天） | 评审通过、无待定字段；`peer_groups` / `anomaly_rules` 非空 |
| **M1 数据层（切片）** | DataSourceAdapter + akshare 适配器、20~30 家含金融/地产切片、原始落盘、单位与口径归一、回算校验、宽表派生 | 3 天 | 切片入库；**L0 按五项检查分别报通过率**（v1.1.1 修正，不再用单一数字/抽样结果代表通过率） |
| **M2 数据层（放量）** | 1500 家拉取、补漏、幂等重跑。**（v1.1.1 决定：后置）** 先跑通 M3→M5 全链路再放量，期间保持 20 家测试切片；期次窗口规则（§3 第 7 条）届时启用 | 1~2 天（多为等待，可并行） | 核心 33 指标覆盖率达标（按模板分别统计） |
| **M3 工具层** | metric_resolver / metric_loader / formula_engine / anomaly_engine + 新工具注册；<br>**（v1.1.1 新增）① 逐条核对数据源映射（重点：新浪摘要"营业成本"口径，见 §3 风险记录）② 补 `metric_dict.formula` 覆盖全部派生指标 ③ RAG 元数据财报化 + 全量向量化** | 2~3 天（核对 +1 天） | L2、L3 首轮跑通，口径解析 Top-1 ≥ 85%；映射核对报告无未决项 |
| **M4 问答** | query_financials（含跨公司对比）+ dupont_analysis / cash_flow_quality + 拒答机制；rag_qa 降级为兜底 | 1~2 天 | 拒答正确率 100% |
| **M5 报告 DAG** | DAG 改造、8 节点编排、白名单溯源、投研合规规则集、**报告模板重新设计（定稿结构见 §6.2）**、docx 输出 | 2~3 天 | 报告 L4 全量通过 |
| **M6 前端** | ECharts 趋势图/对比图 + /api/financial/series + SSE chart 事件 | 1~2 天 | 图表数据 100% 走结构化接口 |
| **M7 评测与回归** | L0~L4 脚本、基线固化、门禁 | 3~4 天（标注从 M0 起并行） | run_all.py 可一键复现全部指标 |

### 关键路径与风险点

- **关键路径**：M0 → M1 → M3 → M5 → M7。M2（拉取）与 M3 可并行，标注全程并行。
- **最大不确定性**：L1 人工标注（40~90 人时）。它不依赖代码，M0 就必须启动；
  若拖到末尾，整个"可评测"卖点会延期。
- **M1 是真正的技术风险点**：akshare 字段探查 + 行业模板 + 口径归一，比 v1.0 估的 2-3 天更满。

---

## 十、风险与对策（v1.1 更新）

| 风险 | 具体表现 | 对策 |
|---|---|---|
| akshare 字段跨版本改名、覆盖不全、限频 | 同一指标不同公司口径不一；科创板/北交所覆盖差 | DataSourceAdapter 抽象 + tushare 备选 + 原始落盘可回放 + 切片先验证 |
| 年报 PDF 解析易错 | 表格跨页、单位混用、脚注口径 | 文本内容后置；指标走结构化接口先行；PDF 仅用于 L1 人工核真值 |
| LLM 算错数 | 自由生 SQL 选错列 / 自行推算术 | 计算全在 SQL/pandas；LLM 只输出结构化参数；query_sql 收窄到两张视图 |
| 报告无来源不可信 | 结论里的数字查不到出处 | §6.3 数字白名单结构性约束 + 脚本校验，非提示词约束 |
| 口径歧义导致"数据正确但解读错误" | "2024 毛利率"含义不唯一 | 期次/单季/合并口径字段化 + 回答强制声明口径 + 低置信澄清 |
| 同行画像被误读为评级 | 用户误读、合规风险 | 只做中性事实比较（本公司值 + 同行中位数/分位）；不产分数与评级；禁用评级词汇；固定免责声明 |
| 行业指标不通用 | 金融业毛利率大片 NULL 污染对比 | 行业指标模板 general/financial/other，按模板分别统计完整率 |
| 评测测了数据源而非系统 | 指标好看但无区分度 | L0 与 L1~L4 分离；系统得分只取 L2~L4 |
| 工期被"2 周"锚定 | 评测层与标注被压缩 | 明确 2 周=演示级、3~4 周=可评测版；标注从 M0 并行启动 |

---

## 附录 A：口径归一化规则速查

| 场景 | 处理 |
|---|---|
| "宁德时代 2024 毛利率" | 取 period_type='FY', is_single_quarter=0, report_scope='consolidated'，回答中声明"2024 年报（累计，合并口径）" |
| "2024Q3 毛利率" | 库中存在累计与单季两个值，**默认取累计**并声明；用户问"单季"才取 is_single_quarter=1 |
| 金额展示 | 库内存"元"，展示按数量级选"万元"或"亿元"，图表轴标注单位 |
| 比率展示 | 库内存小数，展示乘 100 加 "%" |
| 单位来源（v1.1.1） | `unit` 由 `metric_dict.unit_type` 推导：amount→CNY，ratio→ratio（值=小数）、times→times、days→days、per_share→per_share、count→count |
| RAG 文本单位（v1.1.1） | 同一指标**固定单位**：金额一律"亿元"（小额用两位小数），比率一律 `%`，每股"元/股"。禁止按数值大小切换单位（否则同一指标在不同期忽而"元"忽而"亿元"） |
| 期次窗口（v1.1.2 定稿） | **统一近十年**：新浪用 `start_year` 约束、新浪摘要由 `build_reports()` 本地裁剪，窗口外行用 `prune_out_of_window` 清理；原始响应留档可 `replay()` 重建（见 §3 第 7 条） |
| 行业模板（v1.1.2） | `financial` 模板下毛利率、存货/应收应付类及流动性/周转类指标为 N/A：照常入库但不得进文本/对比/画像（见 §3 矩阵） |
| 环比 vs 同比 | 与上一报告期比较时必须写"较上一报告期（环比）"，不得写"同比"；同比只用于同 `period_type` 的上年同期 |
| 同比增长率 | 优先取库内 _yoy 字段；缺失时用同期值计算，并标注"由计算得出" |
| 数据缺失 | 显示"数据缺失"，不插值、不用同行均值填充 |

## 附录 B：首批指标集（metric_dict 种子）

**数量（v1.1.2 更新）**：首批 **33 项核心指标**（v1.1 正文误写"35 项"）；加 5 项长表扩展
（归母净利 / 加权 ROE / 存货·应收·应付周转天数）、**19 项 2026-09-22 扩展批次**（见附录 B-2）、
5 项金融模板专用指标，`metric_dict` 合计 **62 条**（全量进核心宽表，宽表与字典逐列对齐）。
宽表 `financial_core` 共 **59 列** = 3 个键列 + 56 个指标列。

**状态含义（v1.1.2 重定义）**：`已采集` = 库内有数据（附实测覆盖率）；
`口径待定` = **有值但双源口径不一致**，取值随源优先级变化，需单源固定或拆指标（见下方冲突表）；
`待实施` = 公式/迁移未落库；`该源不提供` = 已实测确认接口里没有该字段（2026-09-22 探测，勿再尝试）；
`v1 不采集` = 两个接口都无此字段，需另接数据源。

> 2026-09-22 重拉已完成：原先的"待重拉"状态全部转为实际值（原始响应已是 v2，`raw_financial_payload` 20 行，
> `source` 已标注 sina_summary 21,172 / sina_guide 12,915 / derived 1,442）。覆盖率分母 = 832 期。

| 指标 code | 中文 | 数据源字段 | 派生公式（`metric_dict.formula`） | 状态 |
|---|---|---|---|---|
| revenue | 营业收入 | 新浪摘要「营业总收入」 | — | 已采集 |
| operate_cost | 营业成本 | 新浪「主营业务成本率(%)」→ `operate_cost_ratio` | `revenue * operate_cost_ratio` | 已采集（706/832 = 85%；口径已定案：新浪摘要那行实为营业总成本） |
| gross_profit | 毛利 | — | `revenue - operate_cost` | 已采集（派生，706/832 = 85%） |
| gross_margin | 毛利率 | 新浪摘要「毛利率」/ 新浪「销售毛利率(%)」 | `gross_profit / revenue` | 已采集 |
| net_profit | 净利润 | 新浪摘要「净利润」 | — | 已采集 |
| net_margin | 净利率 | 新浪摘要「销售净利率」/ 新浪「销售净利率(%)」 | `net_profit / revenue` | 已采集 |
| roe | 净资产收益率 | 新浪摘要「净资产收益率(ROE)」/ 新浪「净资产收益率(%)」 | — | 已采集（830/832；**口径待定**：新浪摘要期末 vs 新浪加权，231 处冲突、最大 51pp） |
| roa | 总资产收益率 | 新浪摘要「总资产报酬率(ROA)」/ 新浪「总资产净利润率(%)」 | — | 已采集（828/832；**口径待定**：430 处冲突、最大 10.6pp——"净利润率"与"报酬率"是两个指标） |
| revenue_yoy | 营收同比 | 新浪摘要「营业总收入增长率」/ 新浪「主营业务收入增长率(%)」 | — | 已采集（826/832；**口径待定**：新浪摘要=营业总收入增长率、新浪=主营业务收入增长率，28 处冲突） |
| net_profit_yoy | 净利润同比 | 新浪摘要「归属母公司净利润增长率」/ 新浪「净利润增长率(%)」 | — | 已采集（826/832；**口径翻转已发生**：重拉前取新浪摘要=归母口径、重拉后取新浪=净利润口径） |
| revenue_cagr_3y | 营收三年复合增速 | — | 需跨期依赖 | **v1 未实现** |
| operating_cash_flow | 经营活动现金流净额 | 新浪摘要「经营现金流量净额」 | — | 已采集 |
| investing_cash_flow | 投资活动现金流净额 | — | — | **该源不提供**（新浪摘要无此标签） |
| financing_cash_flow | 筹资活动现金流净额 | — | — | **该源不提供** |
| ocf_to_net_profit | 净现比 | 新浪摘要「经营活动净现金/归属母公司的净利润」/ 新浪「经营现金净流量与净利润的比率**(%)**」——**实测取值为"倍"，列名带 % 但不可 ÷100** | `operating_cash_flow / net_profit` | 已采集（100%；缩放错误已修正；**口径待定**：546 处冲突，新浪摘要分母=归母净利润、新浪=净利润） |
| free_cash_flow | 自由现金流 | — | `operating_cash_flow + investing_cash_flow` | **v1 不采集**（依赖的 `investing_cash_flow` 该源不提供，永远无法派生；建议从核心指标降级） |
| inventory | 存货 | — | — | **该源不提供**（两接口只有周转率/天数，无余额） |
| inventory_turnover | 存货周转率 | 新浪摘要「存货周转率」/ 新浪「存货周转率(次)」 | — | 已采集 |
| receivable | 应收账款 | — | — | **该源不提供**（只有周转率/天数与账龄结构） |
| receivable_turnover | 应收账款周转率 | 新浪摘要 / 新浪「应收账款周转率(次)」 | — | 已采集 |
| payable_turnover | 应付账款周转率 | 新浪摘要「应付账款周转率」 | — | 已采集（新浪侧实测无此列） |
| cash_conversion_cycle | 现金转换周期 | — | `inventory_days + receivable_days - 365 / payable_turnover` | 待实施（公式未落库；`payable_days` 该源不提供，改用 365/应付账款周转率） |
| total_assets | 总资产 | 新浪「总资产(元)」（近十年窗口） | — | 已采集（828/832 = 99.5%；窗口调至十年后已覆盖满） |
| total_liabilities | 总负债 | — | — | **该源不提供**；可由 `total_assets * debt_ratio` 派生（**公式未落库，且须先给 `debt_ratio` 回算加"派生依赖→skip"规则**） |
| total_equity | 股东权益 | 新浪摘要「股东权益合计(净资产)」 | — | 已采集 |
| debt_ratio | 资产负债率 | 新浪摘要 / 新浪「资产负债率(%)」 | `total_liabilities / total_assets` | 已采集 |
| current_ratio | 流动比率 | 新浪摘要 / 新浪「流动比率」 | — | 已采集 |
| quick_ratio | 速动比率 | 新浪摘要 / 新浪「速动比率」 | — | 已采集 |
| interest_coverage | 利息保障倍数 | 新浪「利息支付倍数」 | — | 已采集（718/832 = 86%）；利息净收益公司的负巨值待口径确认 |
| eps | 每股收益 | 新浪摘要「基本每股收益」/ 新浪「摊薄每股收益(元)」 | — | 已采集 |
| bps | 每股净资产 | 新浪摘要「每股净资产」/ 新浪「每股净资产_调整前(元)」 | — | 已采集 |
| dividend_per_share | 每股股利 | 两个接口均无（需分红接口） | — | **v1 不采集** |
| employee_count | 员工人数 | 两个接口均无（需另接接口） | — | **v1 不采集** |

financial 模板首批还需额外定义：interest_income、net_interest_margin、
non_performing_loan_ratio、provision_coverage、capital_adequacy_ratio（字典已有，无数据源）。

**映射核对结论（2026-09-22，`scripts/probe_akshare_fields.py` 实测，样本 600519/300750/600036）**

| 结论 | 内容 |
|---|---|
| 键名命中 | 新浪摘要 37/37、新浪 29/29，映射表已无死映射 |
| 新浪摘要"营业成本"= 营业总成本 | 茅台 2026H1：营收 922.8 亿、该行 309.5 亿，而同分区毛利率 89.56%（真实营业成本约 96 亿）→ 改挂 `total_operating_cost` |
| 未使用的可用字段 | 新浪摘要 33 个、新浪 57 个（含账龄结构 16 列、投资明细等），见 `storage/probe/` 报告 |
| 列名带 % ≠ 百分数 | 实测三例：`经营现金净流量与净利润的比率(%)`（倍）、`经营现金净流量对销售收入比率(%)`（倍）、`股息发放率(%)`（小数）→ 逐列核对量级，不按列名猜 |

**双源口径冲突排行榜（2026-09-22 重拉后，用 raw v2 离线重算，共 1,930 处）**

| 指标 | 冲突数 | 最大偏差 | 典型例（新浪摘要 vs 新浪） | 建议单源 |
|---|---|---|---|---|
| ocf_to_net_profit | 546 | 82.1 | 2023-06-30：−20.78 vs −102.9 | 二选一，**分母口径必须写死**（归母 or 净利润） |
| roa | 430 | 10.6pp | 2024FY：41.86% vs 31.26% | 新浪；新浪摘要那列另存 `roa_ebit` |
| net_profit_yoy | 387 | 26.2 | 2024H1：−6.45 vs −32.64 | 新浪保留；另加 `net_profit_parent_yoy` 采新浪摘要 |
| cash_ratio | 288 | 3.51 | 2019Q3：5.05 vs 1.54 | 新浪摘要（与速动/保守速动同源） |
| roe | 231 | 51.1pp | 2016FY：69.55% vs 18.41% | 新浪摘要；加权口径已由 `roe_weighted` 承载 |
| revenue_yoy | 28 | 98.7pp | 2016FY：3.17% vs 101.9% | 新浪摘要（与 `revenue`=营业总收入同源同口径） |
| net_profit_deducted | 6 | 150 亿 | 2025Q3：64.6 亿 vs 214.9 亿 | 待定（两源语义不同，需逐户核对） |
| 其余（net_margin / current_ratio / debt_ratio / quick_ratio / gross_margin） | 14 | ≤17pp | — | 沿用现规则（比率优先新浪） |
| **合计** | **1,930** | — | — | 待拍板项见 §0.2 第 6 条 |

## 附录 B-2：2026-09-22 扩展批次（19 项，其中 18 项已进核心宽表）

**覆盖率（实测，分母 832 期，2026-09-22 重拉后）**

| 指标 code | 中文 | 数据源 | 单位 | 覆盖率 | 说明 |
|---|---|---|---|---|---|
| total_operating_cost | 营业总成本 | 新浪摘要「营业成本」 | CNY | 100% | 原 operate_cost 承接行 |
| operate_cost_ratio | 主营业务成本率 | 新浪「主营业务成本率(%)」 | ratio | 84.9% | **仅长表**（中间量，用于派生 operate_cost） |
| net_profit_deducted | 扣非净利润 | 新浪摘要「扣非净利润」+ 新浪「扣除非经常性损益后的净利润(元)」 | CNY | 99.8% | 双源；**口径待定**（差最大 150 亿） |
| goodwill | 商誉 | 新浪摘要「商誉」 | CNY | 86.4% | — |
| ocf_per_share | 每股经营现金流 | 新浪摘要「每股经营现金流」+ 新浪「每股经营性现金流(元)」 | per_share | 99.6% | 双源 |
| roe_avg | 净资产收益率(平均) | 新浪摘要「净资产收益率_平均」 | ratio | 99.5% | 与 roe / roe_weighted 构成三口径 |
| roic | 投入资本回报率 | 新浪摘要「投入资本回报率」 | ratio | 84.4% | 金融模板下不适用（待纳入 N/A） |
| operating_margin | 营业利润率 | 新浪摘要「营业利润率」 | ratio | 100% | — |
| effective_tax_rate | 所得税/利润总额 | 新浪摘要「所得税/利润总额」 | ratio | 96.2% | 实际税率 |
| ocf_to_revenue | 经营活动净现金/销售收入 | 新浪摘要「经营活动净现金/销售收入」 | ratio | 100% | 实测为倍，**不 ÷100**；用作"利润现金含量" |
| equity_multiplier | 权益乘数 | 新浪摘要「权益乘数」 | times | 99.5% | 实测为倍；杜邦因子（期末口径） |
| debt_to_equity | 产权比率 | 新浪摘要「产权比率」+ 新浪「产权比率(%)」 | times | 99.5% | 双源；两源均实测为百分数 → ÷100 |
| cash_ratio | 现金比率 | 新浪摘要「现金比率」+ 新浪「现金比率(%)」 | ratio | 84.4% | 双源；新浪摘要给倍、新浪给百分数，各按实测处理；288 处冲突待定 |
| quick_ratio_conservative | 保守速动比率 | 新浪摘要「保守速动比率」 | times | 84.4% | 金融模板下不适用（待纳入 N/A） |
| total_asset_turnover | 总资产周转率 | 新浪摘要「总资产周转率」+ 新浪「总资产周转率(次)」 | times | 99.5% | 双源；杜邦因子（新浪摘要为平均资产口径，与杜邦要求不一致） |
| current_asset_turnover | 流动资产周转率 | 新浪摘要「流动资产周转率」 | times | 84.4% | 金融模板下不适用（待纳入 N/A） |
| fixed_asset_turnover | 固定资产周转率 | 新浪「固定资产周转率(次)」 | times | 41.6% | 仅新浪；覆盖率偏低属正常（重资产行业才有意义） |
| total_assets_yoy | 总资产增长率 | 新浪「总资产增长率(%)」 | ratio | 98.8% | ÷100 |
| equity_yoy | 净资产增长率 | 新浪「净资产增长率(%)」 | ratio | 98.8% | ÷100 |

## 附录 C：合规红线清单

1. 不预测（禁用"预计/有望/将上涨"）
2. 不建议（禁用"买入/增持/推荐/值得配置"）
3. 不评级（禁用"评级/上调评级/自造分级"）
4. 不给目标价、不给收益率预期
5. ~~评分不得命名为"投资价值评分"~~ —— **v1.1.2 已取消评分能力，本条形同失效**；替代条款：
   同行画像不得输出分数、评级或"优于/劣于"结论，只呈现"高于/低于中位数 X 个百分点"的事实
6. 每个数字必须可溯源，无来源不出结论
7. 数据缺失必须显式标注
8. 每份报告固定免责声明："本报告基于公开财务数据的自动化处理结果，仅供研究参考，不构成任何投资建议。"
9. 数据来源与版权：akshare/tushare 转发第三方数据，对外分发需评估授权；年报 PDF 解析文本对外提供需注意版权
10. 杜邦分析不得输出"改善空间 / 提升建议 / 应优化哪一项"——那是隐性投资建议
11. 现金流质量不得表述为"财务造假嫌疑 / 利润粉饰 / 盈余管理"——那是结论性判断，超出事实陈述范围
