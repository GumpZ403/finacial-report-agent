"""domains：业务领域包（每个领域自带 models / repository / tools）

- financial/     财报数据：模型、仓储、采集、质量、文本化、指标查询工具
- retrieval/     检索：Chroma 向量库、BM25 混合检索、重排、RAG 工具
- report/        报告：Plan&Solve 报告图、写作（未来子代理）、合规校验、docx 导出
- conversation/  对话：意图分类、ReAct 循环、主图

领域之间只允许“单向、显式”依赖：
    conversation → harness.registry → (注册进来的工具)
    financial.textify → retrieval.vector   （文本化后直接入向量库）
    report → financial + retrieval + harness
禁止：financial → conversation / interfaces。
"""
