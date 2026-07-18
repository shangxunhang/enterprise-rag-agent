from rag.reader.json_reader import JsonReader
from rag.reader.jsonl_reader import JsonlReader
from rag.reader.txt_reader import TxtReader

READER_MAP = {
    ".txt": TxtReader,
    ".json": JsonReader,
    ".jsonl": JsonlReader,
}