"""financial 领域：财报数据（A 股，东财三表单源）

模块一览（按职责，新增文件请先对号入座）
    models.py          SQLAlchemy 表模型（9 张业务表）
    db.py              引擎 / 会话 / 建表迁移
    repository.py      读写仓储（纯 CRUD）
    formula.py         公式求值（AST 白名单，纯函数）
    display.py         单位语义（UNIT_BY_TYPE）与展示形态（fmt_value/fmt_change）
    fiscal_calendar.py 期次窗口 + 披露节奏（纯函数）
    industry_scope.py  行业模板 × 指标适用性
    quality.py         L0 数据质量校验
    corpus.py          结构化数值 → 检索语料片段
    ingest/            外部数据入口
        eastmoney.py       东财三表采集（akshare 客户端、重试、快照、回放）
        mapping.py         列名映射与归一化（纯函数）
    tools/             注册给 LLM 的工具（文件名 == 工具名）

口径仲裁（方案 §4.3.6）：长表是唯一真相，宽表是镜像；源值优先，回算只校验不覆盖。

**本包不做再导出**：请直接 from app.domains.financial.<模块> import <名字>。
再导出会让包入口变成门面，并制造"import 谁就把谁的依赖全拉起来"的隐式耦合
（例如以前的 __init__ 需要小心避开 corpus 以免把 ChromaDB 拉进 import 期）。
"""
