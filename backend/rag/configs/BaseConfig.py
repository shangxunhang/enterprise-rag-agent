"""定义旧版 Mini-RAG 的项目目录、数据目录和本地模型根路径。

所有项目内路径都从当前文件位置推导，不依赖进程启动目录。这样从 IDE、脚本或
测试启动时，数据与向量库位置保持一致。该模块在导入时会创建 ``VECTOR_STORE_DIR``，
因此它既是常量定义，也是一个轻量文件系统初始化点。
"""

from pathlib import Path


# 当前文件位于 <project>/backend/rag/configs/BaseConfig.py。
CONFIG_DIR = Path(__file__).resolve().parent

# PACKAGE_DIR 对应 backend/rag，SRC_DIR 对应 backend，ROOT_DIR 对应项目根目录。
PACKAGE_DIR = CONFIG_DIR.parent
SRC_DIR = PACKAGE_DIR.parent
ROOT_DIR = SRC_DIR.parent

# 使用更明确名称暴露项目根，保留 ROOT_DIR 供旧调用方兼容。
PROJECT_ROOT = ROOT_DIR

# 所有原始数据、处理中间产物和评测文件的公共数据根目录。
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

# 旧版扁平文档和 Chunk 流水线的默认中间文件。
DOCUMENTS_FILE = PROCESSED_DATA_DIR / "documents.json"
CHUNKS_FILE = PROCESSED_DATA_DIR / "chunks_old.json"

# 本地向量索引根目录。导入配置时按需创建，保证后续写入方有父目录。
VECTOR_STORE_DIR = PROJECT_ROOT / "vector_store"
VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)

# 旧版 FAISS 索引及其 Chunk metadata 文件。
FAISS_INDEX_FILE = VECTOR_STORE_DIR / "faiss.index"
CHUNK_META_FILE = VECTOR_STORE_DIR / "chunk_meta.json"

# 旧版 Milvus Lite 数据库默认文件。
MILVUS_LITE_DB_FILE = VECTOR_STORE_DIR / "milvus_rag.db"

# 本机 Hugging Face 模型总目录；模型类型在 RAGConfig 中继续细分。
MODEL_ROOT_DIR = Path("D:/models/huggingface")


# 直接执行配置文件时打印解析结果，便于排查项目根和启动目录问题。
if __name__ == "__main__":
    print("CONFIG_DIR =", CONFIG_DIR)
    print("PACKAGE_DIR =", PACKAGE_DIR)
    print("SRC_DIR =", SRC_DIR)
    print("PROJECT_ROOT =", PROJECT_ROOT)
    print("VECTOR_STORE_DIR =", VECTOR_STORE_DIR)
    print("MILVUS_LITE_DB_FILE =", MILVUS_LITE_DB_FILE)
