"""
configs/BaseConfig.py
=====================

Mini-RAG 项目的全局路径配置。
所有路径必须基于当前文件位置推导，不能依赖当前启动目录。
"""

from pathlib import Path


# 当前文件：
# D:\MyCode\rag-template\src\rag_template\configs\BaseConfig.py

CONFIG_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = CONFIG_DIR.parent
SRC_DIR = PACKAGE_DIR.parent
ROOT_DIR = SRC_DIR.parent

# 兼容更明确的命名
PROJECT_ROOT = ROOT_DIR

# =========================
# 数据目录
# =========================

DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

# =========================
# 中间产物文件
# =========================

DOCUMENTS_FILE = PROCESSED_DATA_DIR / "documents.json"
CHUNKS_FILE = PROCESSED_DATA_DIR / "chunks_old.json"

# =========================
# 向量库目录
# =========================

VECTOR_STORE_DIR = PROJECT_ROOT / "vector_store"
VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)

FAISS_INDEX_FILE = VECTOR_STORE_DIR / "faiss.index"
CHUNK_META_FILE = VECTOR_STORE_DIR / "chunk_meta.json"

MILVUS_LITE_DB_FILE = VECTOR_STORE_DIR / "milvus_rag.db"

# =========================
# 本地模型路径
# =========================

MODEL_ROOT_DIR = Path("D:/models/huggingface")


if __name__ == "__main__":
    print("CONFIG_DIR =", CONFIG_DIR)
    print("PACKAGE_DIR =", PACKAGE_DIR)
    print("SRC_DIR =", SRC_DIR)
    print("PROJECT_ROOT =", PROJECT_ROOT)
    print("VECTOR_STORE_DIR =", VECTOR_STORE_DIR)
    print("MILVUS_LITE_DB_FILE =", MILVUS_LITE_DB_FILE)