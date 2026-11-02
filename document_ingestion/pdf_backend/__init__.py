"""Legacy PDF ingestion backend ported into the rag-agent repository.

The backend preserves the original decision chain:
MinerU -> shared raw JSONL -> parse evaluation -> PaddleOCR fallback.
Heavy PDF/OCR dependencies are intentionally loaded only by the PDF runtime.
"""

__all__ = ["pdf_parse_pipeline", "mineru_adapter", "paddleocr_adapter", "parse_eval"]
