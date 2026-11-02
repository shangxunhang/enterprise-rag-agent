"""把旧版 RAG 重排、Prompt 构建和模型生成封装为统一评测接口。

``eval_runner`` 只依赖 ``generate(query, retrieved_results)``。本适配器把可选
reranker、prompt_builder 和 llm_generator 串联起来，并以参与 Prompt 的文档 ID
作为第一版引用代理。该引用代理不表示答案中存在显式声明级引用。
"""

from typing import Any, Dict, List

from rag.schema.eval_schema import RetrievalResult


class RAGGeneratorWrapper:
    """为旧版评测 Runner 提供最小完整 RAG 生成链。

    Attributes:
        prompt_builder: 必须提供 ``build_prompt(query, retrieved_results)``。
        llm_generator: 必须提供 ``generate(prompt)``。
        reranker: 可选提供 ``rerank(query, candidates)``。
        rerank_top_k: 可选重排后截断数量。
    """

    def __init__(
        self,
        prompt_builder: Any,
        llm_generator: Any,
        reranker: Any = None,
        rerank_top_k: int | None = None,
    ) -> None:
        """保存生成链依赖和可选重排截断参数。"""

        self.prompt_builder = prompt_builder
        self.llm_generator = llm_generator
        self.reranker = reranker
        self.rerank_top_k = rerank_top_k

    def generate(
        self,
        query: str,
        retrieved_results: List[RetrievalResult],
    ) -> Dict[str, Any]:
        """根据查询和检索结果生成答案及引用文档代理。

        Args:
            query: 原始用户查询。
            retrieved_results: Retriever 返回的标准化结果。

        Returns:
            ``{"answer": str, "cited_doc_ids": list[str]}``，兼容旧版
            ``eval_runner.call_generator``。
        """

        # 第一步：默认直接使用检索顺序；配置 reranker 时先执行重排。
        final_results = retrieved_results
        if self.reranker is not None:
            final_results = self._rerank(
                query=query,
                retrieved_results=retrieved_results,
            )

        # 第二步：使用最终上下文构造 Prompt。
        prompt = self._build_prompt(
            query=query,
            retrieved_results=final_results,
        )

        # 第三步：调用模型生成答案文本。
        answer = self._generate_answer(prompt)

        # 第四步：把进入 Prompt 的去重文档 ID 作为临时引用代理。
        cited_doc_ids = self._extract_cited_doc_ids(final_results)

        return {
            "answer": answer,
            "cited_doc_ids": cited_doc_ids,
        }

    def _rerank(
        self,
        query: str,
        retrieved_results: List[RetrievalResult],
    ) -> List[RetrievalResult]:
        """调用注入的 reranker 并按可选 top_k 截断。

        reranker 接口约定为 ``rerank(query=..., candidates=...)``。如具体实现使用
        字典或其他 Schema，应在注入前增加适配器，而不是让评测 Runner 识别多种接口。
        """

        # 第一步：将标准化结果作为 candidates 交给重排器。
        reranked_results = self.reranker.rerank(
            query=query,
            candidates=retrieved_results,
        )

        # 第二步：仅在显式配置时截断；None 保留重排器全部结果。
        if self.rerank_top_k is not None:
            reranked_results = reranked_results[: self.rerank_top_k]

        return reranked_results

    def _build_prompt(
        self,
        query: str,
        retrieved_results: List[RetrievalResult],
    ) -> str:
        """调用 Prompt Builder 构造模型输入文本。"""

        return self.prompt_builder.build_prompt(
            query=query,
            retrieved_results=retrieved_results,
        )

    def _generate_answer(self, prompt: str) -> str:
        """调用注入的模型生成器返回答案文本。"""

        return self.llm_generator.generate(prompt)

    def _extract_cited_doc_ids(
        self,
        retrieved_results: List[RetrievalResult],
    ) -> List[str]:
        """按上下文顺序提取去重文档 ID 作为引用代理。

        该实现假设进入 Prompt 的结果都可能支持答案，因此只能用于第一版闭环。
        后续应由模型输出显式 citation_id，再绑定到具体声明位置。
        """

        cited_doc_ids: List[str] = []

        # 按最终上下文顺序收集，既去重又保留首次出现顺序。
        for result in retrieved_results:
            if result.doc_id and result.doc_id not in cited_doc_ids:
                cited_doc_ids.append(result.doc_id)

        return cited_doc_ids
