"""在当前工作目录生成一个可独立运行的教学版 Mini-RAG 项目骨架。

脚本创建配置、原始/处理数据、源码、向量库、脚本和测试目录，写入最小 BaseConfig
以及三份示例知识文档。所有文件都采用“存在即跳过”策略，不覆盖用户已经编辑的内容。
"""

from pathlib import Path


# 新项目固定创建在当前工作目录下的 mini_rag 子目录。
PROJECT_NAME = "mini_rag"
ROOT_DIR = Path.cwd() / PROJECT_NAME


# 目录清单覆盖配置、数据、源码、向量产物、脚本和测试六类资源。
DIRS = [
    ROOT_DIR / "configs",
    ROOT_DIR / "data" / "raw",
    ROOT_DIR / "data" / "processed",
    ROOT_DIR / "src",
    ROOT_DIR / "vector_store",
    ROOT_DIR / "scripts",
    ROOT_DIR / "ragtests",
]


# 将被写入新项目 configs/BaseConfig.py 的完整模板。
CONFIG_CONTENT = '''"""Mini-RAG 的路径、分块、Embedding 和检索默认配置。"""

from pathlib import Path


# 配置文件位于 <project>/configs/BaseConfig.py，因此父目录的父目录是项目根。
ROOT_DIR = Path(__file__).resolve().parent.parent

# 原始数据和离线处理中间产物目录。
DATA_DIR = ROOT_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

# Reader 和 Chunker 的默认 JSON 中间文件。
DOCUMENTS_FILE = PROCESSED_DATA_DIR / "documents.json"
CHUNKS_FILE = PROCESSED_DATA_DIR / "chunks_old.json"

# FAISS 向量索引和按行对应的 Chunk metadata。
VECTOR_STORE_DIR = ROOT_DIR / "vector_store"
FAISS_INDEX_FILE = VECTOR_STORE_DIR / "faiss.index"
CHUNK_META_FILE = VECTOR_STORE_DIR / "chunk_meta.json"

# 固定窗口分块参数，单位为字符。
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# 教学项目默认使用多语言 Sentence Transformer，可替换为本地中文模型路径。
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Retriever 默认返回的结果数。
TOP_K = 3
'''


# 三份示例文档分别覆盖 RAG、Embedding 和 FAISS 基础概念。
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
    """递归创建项目所需目录，已存在目录保持不变。"""

    # 按声明顺序创建；parents=True 允许一次创建 data/raw 等多级路径。
    for directory in DIRS:
        directory.mkdir(parents=True, exist_ok=True)


def create_file(file_path: Path, content: str = "") -> None:
    """仅在目标不存在时创建 UTF-8 文件。

    Args:
        file_path: 目标文件路径。
        content: 初始文本内容，默认创建空文件。

    已存在文件会打印 Skip 并保持原内容，保证重复运行脚本不会覆盖用户代码或数据。
    """

    # 第一步：保护现有文件，不执行覆盖写入。
    if file_path.exists():
        print(f"[Skip] 文件已存在: {file_path}")
        return

    # 第二步：创建父目录，兼容调用方传入不在预声明 DIRS 中的文件。
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 第三步：以 UTF-8 写入初始内容。
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(content)

    print(f"[Create] 文件已创建: {file_path}")


def main() -> None:
    """执行完整 Mini-RAG 项目初始化流程。"""

    # 第一步：展示实际目标根目录，避免在错误工作目录中生成项目而不自知。
    print(f"[Init] 项目根目录: {ROOT_DIR}")

    # 第二步：创建目录骨架。
    create_dirs()

    # 第三步：创建 Python 包标记文件。
    create_file(ROOT_DIR / "__init__.py")
    create_file(ROOT_DIR / "configs" / "__init__.py")
    create_file(ROOT_DIR / "src" / "__init__.py")

    # 第四步：写入最小全局配置。
    create_file(
        ROOT_DIR / "configs" / "BaseConfig.py",
        CONFIG_CONTENT,
    )

    # 第五步：写入三份可立即用于 Reader/Chunker/FAISS 教学链的原始文本。
    create_file(
        ROOT_DIR / "data" / "raw" / "doc_001.txt",
        DOC_001,
    )
    create_file(
        ROOT_DIR / "data" / "raw" / "doc_002.txt",
        DOC_002,
    )
    create_file(
        ROOT_DIR / "data" / "raw" / "doc_003.txt",
        DOC_003,
    )

    # 第六步：提示用户进入新项目目录继续后续步骤。
    print("\n[Done] Mini-RAG 项目初始化完成。")
    print("下一步进入项目目录：")
    print(f"cd {PROJECT_NAME}")


# 仅直接运行脚本时创建文件，模块导入不会产生文件系统副作用。
if __name__ == "__main__":
    main()
