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
    print("=" * 80)
    print("[eval_milvus] 初始化 TextEmbedder")
    print("=" * 80)

    embedder = TextEmbedder(
        model_name=EMBEDDING_MODEL_NAME,
        device=EMBEDDING_DEVICE,
        batch_size=EMBEDDING_BATCH_SIZE,
    )

    print("=" * 80)
    print("[eval_milvus] 初始化 MilvusRetriever")
    print("=" * 80)

    retriever = MilvusRetriever(
        db_file=MILVUS_LITE_DB_FILE,
        collection_name=MILVUS_COLLECTION_NAME,
        dim=MILVUS_DIM,
        embedder=embedder,
    )

    eval_dataset_path = PROCESSED_DATA_DIR / "eval_dataset.json"
    eval_report_path = PROCESSED_DATA_DIR / "eval_milvus_report.json"

    print("=" * 80)
    print("[eval_milvus] 开始运行 Milvus 检索评估")
    print(f"[eval_milvus] eval_dataset_path = {eval_dataset_path}")
    print(f"[eval_milvus] eval_report_path = {eval_report_path}")
    print(f"[eval_milvus] top_k = {TOP_K}")
    print("=" * 80)

    report = run_and_save_eval(
        retriever=retriever,
        eval_dataset_path=eval_dataset_path,
        output_path=eval_report_path,
        top_k=TOP_K,
        generator=None,
    )

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


if __name__ == "__main__":
    main()