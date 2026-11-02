"""使用项目默认配置运行一次旧版 Milvus 检索评测。

该脚本是手工 CLI 入口：装载本地 Embedding 模型和 Milvus Lite Retriever，读取
``PROCESSED_DATA_DIR/eval_dataset.json``，执行纯检索评测并把报告写入同一目录。
它不会启动生成器，因此报告中的生成指标不参与本次运行。
"""

from rag.configs.RAGConfig import (
    EMBEDDING_MODEL_NAME,
    EMBEDDING_DEVICE,
    EMBEDDING_BATCH_SIZE,
    MILVUS_LITE_DB_FILE,
    MILVUS_COLLECTION_NAME,
    MILVUS_DIM,
    PROCESSED_DATA_DIR,
    TOP_K,
)

from rag.embed.embedder import TextEmbedder
from rag.retriever.milvus_retriever import MilvusRetriever
from eval.rag.engine.eval_runner import run_and_save_eval


def main() -> None:
    """装配默认 Milvus 检索链并执行评测。"""

    # 第一步：根据统一 RAG 配置加载文本向量模型。
    print("=" * 80)
    print("[eval_milvus] 初始化 TextEmbedder")
    print("=" * 80)
    embedder = TextEmbedder(
        model_name=EMBEDDING_MODEL_NAME,
        device=EMBEDDING_DEVICE,
        batch_size=EMBEDDING_BATCH_SIZE,
    )

    # 第二步：使用同一向量维度、数据库文件和集合名称创建 Milvus Retriever。
    print("=" * 80)
    print("[eval_milvus] 初始化 MilvusRetriever")
    print("=" * 80)
    retriever = MilvusRetriever(
        db_file=MILVUS_LITE_DB_FILE,
        collection_name=MILVUS_COLLECTION_NAME,
        dim=MILVUS_DIM,
        embedder=embedder,
    )

    # 第三步：推导默认黄金数据集和报告文件位置。
    eval_dataset_path = PROCESSED_DATA_DIR / "eval_dataset.json"
    eval_report_path = PROCESSED_DATA_DIR / "eval_milvus_report.json"

    # 第四步：打印本次实际输入、输出和 top_k，便于命令行运行留痕。
    print("=" * 80)
    print("[eval_milvus] 开始运行 Milvus 检索评估")
    print(f"[eval_milvus] eval_dataset_path = {eval_dataset_path}")
    print(f"[eval_milvus] eval_report_path = {eval_report_path}")
    print(f"[eval_milvus] top_k = {TOP_K}")
    print("=" * 80)

    # 第五步：执行纯检索评测。generator=None 明确禁用回答生成阶段。
    report = run_and_save_eval(
        retriever=retriever,
        eval_dataset_path=eval_dataset_path,
        output_path=eval_report_path,
        top_k=TOP_K,
        generator=None,
    )

    # 第六步：输出样本规模和核心聚合指标，完整明细仍以报告文件为准。
    print("=" * 80)
    print("[eval_milvus] 评估完成")
    print(f"[eval_milvus] num_samples = {report.num_samples}")
    print(f"[eval_milvus] top_k = {report.top_k}")
    print(f"[eval_milvus] avg_hit_at_k = {report.avg_hit_at_k:.4f}")
    print(f"[eval_milvus] avg_recall_at_k = {report.avg_recall_at_k:.4f}")
    print(f"[eval_milvus] avg_mrr = {report.avg_mrr:.4f}")
    print(
        f"[eval_milvus] avg_context_keyword_hit = "
        f"{report.avg_context_keyword_hit:.4f}"
    )
    print(f"[eval_milvus] report saved to: {eval_report_path}")
    print("=" * 80)


# 仅在脚本直接执行时启动评测，作为模块导入不会加载模型或连接 Milvus。
if __name__ == "__main__":
    main()
