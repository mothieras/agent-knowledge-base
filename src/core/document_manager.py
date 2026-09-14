import re
import shutil
import tempfile
from pathlib import Path
import config
from utils import pdf_to_markdown, clear_directory_contents

SUPPORTED_SUFFIXES = (".pdf", ".md", ".txt")

# page_separators=True 的页界标记：无文本层 PDF 的产物只剩它，空文本判定须剥除
PAGE_SEPARATOR_RE = re.compile(r"--- end of page\.page_number=\d+ ---")


class _IngestError(Exception):
    """带终态类别的入库失败（类别清单见 PHASE1 §5 T3）。"""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


class DocumentManager:

    def __init__(self, chunker, parent_store, vector_db, collection_name):
        self.chunker = chunker
        self.parent_store = parent_store
        self.vector_db = vector_db
        self.collection_name = collection_name
        self.markdown_dir = Path(config.MARKDOWN_DIR)
        self.markdown_dir.mkdir(parents=True, exist_ok=True)

    def add_documents(self, document_paths, progress_callback=None, source_names=None, doc_meta=None):
        if isinstance(document_paths, str):
            document_paths = [document_paths]
        results = []

        for i, doc_path in enumerate(document_paths or []):
            if progress_callback:
                progress_callback((i + 1) / len(document_paths), f"Processing {Path(doc_path).name}")

            source_path = Path(doc_path)
            source_name = source_names.get(doc_path) if source_names else None
            record = {"path": str(doc_path), "source": source_name or source_path.name,
                      "status": "parse_error", "detail": ""}

            if source_path.suffix.lower() not in SUPPORTED_SUFFIXES:
                record.update(status="unsupported_format",
                              detail=f"后缀 {source_path.suffix or '(无)'} 不在支持列表 {list(SUPPORTED_SUFFIXES)}")
                results.append(record)
                continue

            # source_name 提供时用 slug 做扁平文件名，避免同 stem 冲突
            # (语料有 3 篇 README.md、2 篇 pom.xml；现有按 stem 命名会互相跳过)
            doc_name = source_name.replace("/", "__") if source_name else source_path.stem
            md_path = self.markdown_dir / f"{doc_name}.md"

            if md_path.exists():
                self._classify_existing(source_path, md_path, record)
                results.append(record)
                continue

            parent_ids = []
            try:
                if source_path.suffix.lower() == ".md":
                    if not source_path.read_bytes().strip():
                        raise _IngestError("empty_text", "文件无文本内容（空或仅空白）")
                    shutil.copy(source_path, md_path)
                elif source_path.suffix.lower() == ".txt":
                    try:
                        text = source_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError as e:
                        raise _IngestError("unsupported_encoding", f"非 UTF-8 文本: {e}")
                    if not text.strip():
                        raise _IngestError("empty_text", "文件无文本内容（空或仅空白）")
                    # TXT 按 UTF-8 规范化导入；内容直接作为无标题纯文本分块
                    md_path.write_text(text, encoding="utf-8")
                else:
                    text = self._convert_pdf(source_path, md_path)
                    if not PAGE_SEPARATOR_RE.sub("", text).strip():
                        raise _IngestError("no_text_layer", "PDF 无文本层（扫描件；OCR 未启用）")

                parent_chunks, child_chunks = self.chunker.create_chunks_single(
                    md_path,
                    source_name=source_name or source_path.name,
                    doc_meta=(doc_meta or {}).get(doc_path),
                )

                if not child_chunks:
                    raise _IngestError("empty_text", "规范化后无可分块文本")

                parent_ids = [parent_id for parent_id, _ in parent_chunks]
                self.parent_store.save_many(parent_chunks)
                collection = self.vector_db.get_collection(self.collection_name)
                collection.add_documents(child_chunks)

                record["status"] = "ok"

            except _IngestError as e:
                self._rollback(parent_ids, md_path)
                record.update(status=e.status, detail=str(e))
            except Exception as e:
                self._rollback(parent_ids, md_path)
                record.update(status="parse_error", detail=f"{type(e).__name__}: {e}")

            results.append(record)

        return results

    def _classify_existing(self, source_path, md_path, record):
        """同名目标已存在：内容一致 = duplicate，不一致 = conflict（PHASE1 Q3：
        manifest 治理下的登记错误，静默跳过会掩盖它）。"""
        try:
            same = self._same_product(source_path, md_path)
        except UnicodeDecodeError as e:
            record.update(status="unsupported_encoding", detail=f"非 UTF-8 文本: {e}")
            return
        except Exception as e:
            record.update(status="parse_error", detail=f"{type(e).__name__}: {e}")
            return

        if same:
            record.update(status="duplicate", detail=f"目标 {md_path.name} 已存在且内容一致")
        else:
            record.update(status="conflict", detail=f"目标 {md_path.name} 已存在且内容不同（同名不同来源）")

    def _same_product(self, source_path, md_path):
        suffix = source_path.suffix.lower()
        if suffix == ".md":
            return md_path.read_bytes() == source_path.read_bytes()
        if suffix == ".txt":
            return md_path.read_text(encoding="utf-8") == source_path.read_text(encoding="utf-8")
        # PDF：重转换到临时目录比对，不触碰已入库产物
        with tempfile.TemporaryDirectory() as td:
            pdf_to_markdown(str(source_path), td)
            converted = (Path(td) / source_path.stem).with_suffix(".md")
            return converted.read_text(encoding="utf-8") == md_path.read_text(encoding="utf-8")

    def _convert_pdf(self, source_path, md_path):
        """转换并落位到目标名：转换器按 stem 写出，source_name 的 slug 路径须归位。"""
        pdf_to_markdown(str(source_path), self.markdown_dir)
        stem_md = (self.markdown_dir / source_path.stem).with_suffix(".md")
        if stem_md != md_path:
            shutil.move(str(stem_md), str(md_path))
        return md_path.read_text(encoding="utf-8")

    def _rollback(self, parent_ids, md_path):
        self.parent_store.delete_many(parent_ids)
        if md_path.exists():
            md_path.unlink()

    def get_markdown_files(self):
        sources = self.parent_store.list_sources()
        if sources:
            return sources
        return sorted(p.name for p in self.markdown_dir.glob("*.md"))

    def clear_all(self):
        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        self.vector_db.delete_collection(self.collection_name)

        clear_directory_contents(self.markdown_dir)
        self.parent_store.clear_store()

        self.vector_db.create_collection(self.collection_name)
