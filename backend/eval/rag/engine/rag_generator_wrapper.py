"""
rag_generator_wrapper.py
========================

用于 eval 阶段的完整 RAG 生成器封装。

它把 reranker、prompt_builder、llm_generator 串起来，
并对 eval_runner 暴露统一 generate(query, retrieved_results) 接口。
"""

from typing import Any, Dict, List

from rag.legacy.schema.eval_schema import RetrievalResult


class RAGGeneratorWrapper:
    """
    完整 RAG 生成器封装。

    eval_runner 只认识 generator.generate(query, retrieved_results)，
    但真实 RAG 生成流程通常包括：
    1. rerank
    2. prompt build
    3. llm generate
    4. source/citation 提取

    所以这里用 wrapper 把完整生成链路封装起来。
    """

    def __init__(
        self,
        prompt_builder: Any,
        llm_generator: Any,
        reranker: Any = None,
        rerank_top_k: int | None = None,
    ):
        self.prompt_builder = prompt_builder
        self.llm_generator = llm_generator
        self.reranker = reranker
        self.rerank_top_k = rerank_top_k

    def generate(
        self,
        query: str,
        retrieved_results: List[RetrievalResult],
    ) -> Dict[str, Any]:
        """
        根据 query 和 retrieved_results 生成最终答案。

        返回格式必须兼容 eval_runner.call_generator：

        {
            "answer": "...",
            "cited_doc_ids": ["doc_001", "doc_002"]
        }
        """

        final_results = retrieved_results

        if self.reranker is not None:
            final_results = self._rerank(
                query=query,
                retrieved_results=retrieved_results,
            )

        prompt = self._build_prompt(
            query=query,
            retrieved_results=final_results,
        )

        answer = self._generate_answer(prompt)

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
        """
        调用 reranker 对检索结果重排。

        这里要根据你现有 reranker 的真实接口微调。
        """

        # 如果你的 reranker 接收 dict/list，需要在这里转换
        reranked_results = self.reranker.rerank(
            query=query,
            candidates=retrieved_results,
        )

        if self.rerank_top_k is not None:
            reranked_results = reranked_results[: self.rerank_top_k]

        return reranked_results

    def _build_prompt(
        self,
        query: str,
        retrieved_results: List[RetrievalResult],
    ) -> str:
        """
        调用 prompt_builder 构造 prompt。

        这里也要根据你现有 PromptBuilder 的真实接口微调。
        """

        return self.prompt_builder.build_prompt(
            query=query,
            retrieved_results=retrieved_results,
        )

    def _generate_answer(self, prompt: str) -> str:
        """
        调用 LLM 生成答案。

        这里要根据你现有 LLM 类的真实接口微调。
        """

        return self.llm_generator.generate(prompt)

    def _extract_cited_doc_ids(
        self,
        retrieved_results: List[RetrievalResult],
    ) -> List[str]:
        """
        第一版 citation 简化处理：

        只要 answer 是基于这些 retrieved_results 生成的，
        就先把参与 prompt 的 doc_id 当成 cited_doc_ids。

        后续可以升级为：
        1. answer 显式引用 chunk_id
        2. prompt 要求模型输出 sources
        3. 从 answer 中解析引用标记
        """

        cited_doc_ids = []

        for result in retrieved_results:
            if result.doc_id and result.doc_id not in cited_doc_ids:
                cited_doc_ids.append(result.doc_id)

        return cited_doc_ids