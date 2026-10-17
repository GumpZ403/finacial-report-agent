"""retrieval 领域：财报文本检索

- vector.py     ChromaDB 向量库封装（financial_chunks 集合）
- hybrid.py     混合检索（向量 + BM25，min-max 归一加权）
- reranker.py   Cross-Encoder 重排
- tools.py      注册给 LLM 的 rag_search 工具（只检索不生成）

纪律：向量库只从这里访问；textify（financial 领域）通过 vector.py 写入。
"""
