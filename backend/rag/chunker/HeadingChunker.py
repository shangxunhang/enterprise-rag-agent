# src/rag_template/chunker/HeadingChunker.py
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rag.chunker.base_chunker import BaseChunker
from rag.chunker.RecursiveChunker import RecursiveChunker
from rag.legacy.schema.Chunk_Schema import build_chunk


@dataclass
class SectionBlock:
    title: Optional[str]
    level: Optional[int]
    section_path: Optional[str]
    text: str
    start_char: Optional[int]
    end_char: Optional[int]


class HeadingChunker(BaseChunker):
    """
    标题 / 章节切分器。

    支持常见结构：
    - Markdown: # / ## / ###
    - 中文政策/书籍: 第一篇 / 第一章 / 第一节 / 第一条
    - 中文编号: 一、二、三、
    - 中文括号编号: （一）（二）
    - 数字编号: 1. / 1.1 / 1.1.1 / 3.2 填写基础信息

    超长 section 会交给 Token 级 RecursiveChunker 兜底拆分，同时保留 section / section_path。
    """

    MARKDOWN_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    CN_PART_RE = re.compile(r"^第[一二三四五六七八九十百千万零〇0-9]+篇\s*.*")
    CN_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千万零〇0-9]+章\s*.*")
    CN_SECTION_RE = re.compile(r"^第[一二三四五六七八九十百千万零〇0-9]+节\s*.*")
    CN_ARTICLE_RE = re.compile(r"^第\s*[一二三四五六七八九十百千万零〇0-9]+\s*条\s*.*")
    CN_ENUM_RE = re.compile(r"^[一二三四五六七八九十]+、\s*.*")
    CN_PAREN_ENUM_RE = re.compile(r"^（[一二三四五六七八九十]+）\s*.*")
    NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[\.、)]|\s+)\s*.+")

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        super().__init__(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self.recursive_chunker = RecursiveChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk_document(self, document: Dict) -> List[Dict]:
        doc_id = document["doc_id"]
        text = document.get("text", "")
        doc_metadata = document.get("metadata", {})

        sections = self.split_sections(text)
        chunks: List[Dict] = []
        chunk_index = 0

        for section_idx, section in enumerate(sections):
            if not section.text.strip():
                continue

            parent_chunk_id = f"{doc_id}_section_{section_idx:04d}"

            if self.recursive_chunker.token_count(section.text) <= self.chunk_size:
                section_text = section.text.strip()
                chunks.append(
                    build_chunk(
                        doc_id=doc_id,
                        text=section_text,
                        idx=chunk_index,
                        doc_metadata=doc_metadata,
                        chunk_type="heading",
                        section=section.title,
                        section_path=section.section_path,
                        parent_chunk_id=None,
                        start_char=section.start_char,
                        end_char=section.end_char,
                        heading_level=section.level,
                        token_count=self.recursive_chunker.token_count(section_text),
                        extra={
                            "section_index": section_idx,
                            "chunk_unit": "token",
                            "tokenizer_backend": self.recursive_chunker.token_counter.backend,
                        },
                    )
                )
                chunk_index += 1
                continue

            # section 过长：递归拆分，但保留章节元数据。
            sub_pieces = self.recursive_chunker.split_text_with_offsets(section.text)
            for sub_idx, (sub_text, rel_start, rel_end) in enumerate(sub_pieces):
                abs_start = None if section.start_char is None or rel_start is None else section.start_char + rel_start
                abs_end = None if section.start_char is None or rel_end is None else section.start_char + rel_end

                chunks.append(
                    build_chunk(
                        doc_id=doc_id,
                        text=sub_text.strip(),
                        idx=chunk_index,
                        doc_metadata=doc_metadata,
                        chunk_type="heading_recursive",
                        section=section.title,
                        section_path=section.section_path,
                        parent_chunk_id=parent_chunk_id,
                        start_char=abs_start,
                        end_char=abs_end,
                        heading_level=section.level,
                        token_count=self.recursive_chunker.token_count(sub_text),
                        extra={
                            "section_index": section_idx,
                            "section_sub_index": sub_idx,
                            "chunk_unit": "token",
                            "tokenizer_backend": self.recursive_chunker.token_counter.backend,
                        },
                    )
                )
                chunk_index += 1

        return chunks

    def split_sections(self, text: str) -> List[SectionBlock]:
        if not text or not text.strip():
            return []

        lines_with_offsets = self._iter_lines_with_offsets(text)
        sections: List[SectionBlock] = []
        heading_stack: List[Tuple[int, str]] = []

        current_title: Optional[str] = None
        current_level: Optional[int] = None
        current_path: Optional[str] = None
        current_lines: List[str] = []
        current_start: Optional[int] = None
        current_end: Optional[int] = None

        def flush_current():
            nonlocal current_lines, current_title, current_level, current_path, current_start, current_end
            block_text = "\n".join(current_lines).strip()
            if block_text:
                sections.append(
                    SectionBlock(
                        title=current_title,
                        level=current_level,
                        section_path=current_path,
                        text=block_text,
                        start_char=current_start,
                        end_char=current_end,
                    )
                )
            current_lines = []
            current_start = None
            current_end = None

        for line, start, end in lines_with_offsets:
            stripped = line.strip()
            heading = self.detect_heading(stripped) if stripped else None

            if heading:
                flush_current()

                level, title = heading
                heading_stack = [(lv, t) for lv, t in heading_stack if lv < level]
                heading_stack.append((level, title))

                current_title = title
                current_level = level
                current_path = " > ".join(t for _, t in heading_stack)
                current_lines = [line]
                current_start = start
                current_end = end
            else:
                if current_start is None:
                    current_start = start
                current_lines.append(line)
                current_end = end

        flush_current()
        return sections

    def detect_heading(self, line: str) -> Optional[Tuple[int, str]]:
        if not line:
            return None

        md = self.MARKDOWN_RE.match(line)
        if md:
            return len(md.group(1)), line

        normalized = re.sub(r"\s+", "", line)

        if self.CN_PART_RE.match(normalized):
            return 1, line
        if self.CN_CHAPTER_RE.match(normalized):
            return 2, line
        if self.CN_SECTION_RE.match(normalized):
            return 3, line
        if self.CN_ARTICLE_RE.match(normalized):
            return 4, line
        if self.CN_ENUM_RE.match(line):
            return 3, line
        if self.CN_PAREN_ENUM_RE.match(line):
            return 4, line

        numbered = self.NUMBERED_RE.match(line)
        if numbered:
            number = numbered.group(1)
            # 1 -> 2, 1.1 -> 3, 1.1.1 -> 4；避免压过 Markdown 一级标题。
            return min(number.count(".") + 2, 6), line

        return None

    def _iter_lines_with_offsets(self, text: str) -> List[Tuple[str, int, int]]:
        lines: List[Tuple[str, int, int]] = []
        cursor = 0
        for raw_line in text.splitlines():
            start = cursor
            end = start + len(raw_line)
            lines.append((raw_line, start, end))
            cursor = end + 1
        return lines
