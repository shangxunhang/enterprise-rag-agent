"""
init_mini_rag.py
================

Mini-RAG 项目初始化脚本。

功能：
1. 创建 mini_rag 项目目录
2. 创建 configs / data / src / scripts / ragtests / vector_store 等目录
3. 创建必要的 __init__.py 文件
4. 自动生成 configs/BaseConfig.py
5. 创建 3 个示例 txt 文档

运行方式：
    python init_mini_rag.py
"""

from pathlib import Path


# =========================
# 1. 项目根目录名称
# =========================

PROJECT_NAME = "mini_rag"
ROOT_DIR = Path.cwd() / PROJECT_NAME


# =========================
# 2. 需要创建的目录
# =========================

DIRS = [
    ROOT_DIR / "configs",
    ROOT_DIR / "data" / "raw",
    ROOT_DIR / "data" / "processed",
    ROOT_DIR / "src",
    ROOT_DIR / "vector_store",
    ROOT_DIR / "scripts",
    ROOT_DIR / "ragtests",
]


# =========================
# 3. BaseConfig.py 内容
# =========================

CONFIG_CONTENT = '''"""
configs/BaseConfig.py
=================

Mini-RAG 项目的全局配置文件。

它只负责定义：
1. 项目路径
2. 数据目录
3. 中间产物路径
4. 向量库路径
5. chunk 参数
6. embedding 模型配置
7. 检索参数

不负责执行业务逻辑。
"""

from pathlib import Path


# =========================
# 1. 项目根目录
# =========================

# 当前文件位置：
# mini_rag/configs/BaseConfig.py
# parent        -> configs/
# parent.parent -> mini_rag/
ROOT_DIR = Path(__file__).resolve().parent.parent


# =========================
# 2. 数据目录
# =========================

DATA_DIR = ROOT_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"


# =========================
# 3. 中间产物文件
# =========================

DOCUMENTS_FILE = PROCESSED_DATA_DIR / "documents.json"
CHUNKS_FILE = PROCESSED_DATA_DIR / "chunks_old.json"


# =========================
# 4. 向量库目录
# =========================

VECTOR_STORE_DIR = ROOT_DIR / "vector_store"

FAISS_INDEX_FILE = VECTOR_STORE_DIR / "faiss.index"
CHUNK_META_FILE = VECTOR_STORE_DIR / "chunk_meta.json"


# =========================
# 5. Chunk 参数
# =========================

# 第一版先使用固定长度切分
CHUNK_SIZE = 500

# 相邻 chunk 的重叠字符数
CHUNK_OVERLAP = 50


# =========================
# 6. Embedding 模型
# =========================

# 第一版使用多语言 sentence-transformers 模型
# 后面可以替换为中文效果更强的 bge-small-zh / bge-base-zh
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# =========================
# 7. 检索参数
# =========================

TOP_K = 3
'''


# =========================
# 4. 示例文档内容
# =========================

DOC_001 = """RAG 是 Retrieval-Augmented Generation 的缩写，中文通常称为检索增强生成。

RAG 的核心思想是：在大模型生成答案之前，先从外部知识库中检索相关资料，然后把这些资料和用户问题一起放入 prompt 中，让大模型基于资料回答。

RAG 适合企业知识库问答、政策制度查询、技术文档问答、客服问答等场景。
"""

DOC_002 = """Embedding 是把文本转换成向量的过程。

在 RAG 系统中，文档 chunk 和用户 query 都会被 embedding 模型编码成向量。然后系统通过余弦相似度或内积计算 query 和 chunk 的语义相似度。

如果 query 和某个 chunk 语义接近，它们在向量空间中的距离通常也会更近。
"""

DOC_003 = """FAISS 是 Facebook AI Research 开源的向量检索库。

在 Mini-RAG 项目中，FAISS 可以用来保存所有 chunk 的 embedding，并根据用户 query 的 embedding 快速检索最相似的 chunk。

FAISS 本身主要保存向量索引，chunk 的原文内容、来源文件、chunk_id 等 metadata 通常需要额外保存。
"""


def create_dirs() -> None:
    """
    创建项目目录。
    """
    for dir_path in DIRS:
        dir_path.mkdir(parents=True, exist_ok=True)


def create_file(file_path: Path, content: str = "") -> None:
    """
    创建文件。

    如果文件已经存在，则不覆盖，避免误删已有内容。

    Args:
        file_path: 文件路径
        content: 文件内容
    """
    if file_path.exists():
        print(f"[Skip] 文件已存在: {file_path}")
        return

    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[Create] 文件已创建: {file_path}")


def main() -> None:
    """
    初始化 Mini-RAG 项目。
    """
    print(f"[Init] 项目根目录: {ROOT_DIR}")

    # 1. 创建目录
    create_dirs()

    # 2. 创建 __init__.py
    create_file(ROOT_DIR / "__init__.py")
    create_file(ROOT_DIR / "configs" / "__init__.py")
    create_file(ROOT_DIR / "src" / "__init__.py")

    # 3. 创建 BaseConfig.py
    create_file(ROOT_DIR / "configs" / "BaseConfig.py", CONFIG_CONTENT)

    # 4. 创建示例 txt 文档
    create_file(ROOT_DIR / "data" / "raw" / "doc_001.txt", DOC_001)
    create_file(ROOT_DIR / "data" / "raw" / "doc_002.txt", DOC_002)
    create_file(ROOT_DIR / "data" / "raw" / "doc_003.txt", DOC_003)

    print("\n[Done] Mini-RAG 项目初始化完成。")
    print("下一步进入项目目录：")
    print(f"cd {PROJECT_NAME}")


if __name__ == "__main__":
    main()