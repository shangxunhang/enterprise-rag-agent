# 语义切分
from typing import Dict, List

from rag.chunker.base_chunker import BaseChunker


class SemanticChunker(BaseChunker):

    def chunk_document(self, document: Dict) -> List[Dict]:
        return None