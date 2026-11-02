"""声明文件扩展名到 Reader 实现的静态映射。"""

from rag.reader.json_reader import JsonReader
from rag.reader.jsonl_reader import JsonlReader
from rag.reader.txt_reader import TxtReader

# ReaderFactory 只根据小写扩展名查表；新增格式需要在此注册对应 Reader 类。
READER_MAP = {
    ".txt": TxtReader,
    ".json": JsonReader,
    ".jsonl": JsonlReader,
}
