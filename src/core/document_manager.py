from pathlib import Path
import shutil
import config
from utils import pdfs_to_markdowns, clear_directory_contents

class DocumentManager:

    def __init__(self, rag_system):
        self.rag_system = rag_system
        self.markdown_dir = Path(config.MARKDOWN_DIR)
        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        
    def add_documents(self, document_paths, progress_callback=None, source_names=None, doc_meta=None):
        if not document_paths:
            return 0, 0
            
        document_paths = [document_paths] if isinstance(document_paths, str) else document_paths
        document_paths = [
            p for p in document_paths
            if p and Path(p).suffix.lower() in [".pdf", ".md", ".txt"]
        ]
        
        if not document_paths:
            return 0, 0
            
        added = 0
        skipped = 0
            
        for i, doc_path in enumerate(document_paths):
            if progress_callback:
                progress_callback((i + 1) / len(document_paths), f"Processing {Path(doc_path).name}")
                
            source_path = Path(doc_path)
            source_name = source_names.get(doc_path) if source_names else None
            # source_name 提供时用 slug 做扁平文件名，避免同 stem 冲突
            # (语料有 3 篇 README.md、2 篇 pom.xml；现有按 stem 命名会互相跳过)
            doc_name = source_name.replace("/", "__") if source_name else source_path.stem
            md_path = self.markdown_dir / f"{doc_name}.md"
            
            if md_path.exists():
                skipped += 1
                continue
                
            parent_ids = []
            try:
                if source_path.suffix.lower() == ".md":
                    shutil.copy(source_path, md_path)
                elif source_path.suffix.lower() == ".txt":
                    # TXT 按 UTF-8 规范化导入；内容直接作为无标题纯文本分块
                    md_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
                else:
                    pdfs_to_markdowns(str(source_path), overwrite=False)
                    extracted = md_path.read_text(encoding="utf-8").strip()
                    if not extracted:
                        # 扫描件/无文本层 PDF 不是"导入成功"
                        raise ValueError("PDF 未提取出可用文本（扫描件/OCR 不支持）")
                parent_chunks, child_chunks = self.rag_system.chunker.create_chunks_single(
                    md_path,
                    source_name=source_name or source_path.name,
                    doc_meta=(doc_meta or {}).get(doc_path),
                )
                
                if not child_chunks:
                    raise ValueError("No child chunks were created.")
                
                parent_ids = [parent_id for parent_id, _ in parent_chunks]
                self.rag_system.parent_store.save_many(parent_chunks)
                collection = self.rag_system.vector_db.get_collection(self.rag_system.collection_name)
                collection.add_documents(child_chunks)
                
                added += 1
                
            except Exception as e:
                self.rag_system.parent_store.delete_many(parent_ids)
                if md_path.exists():
                    md_path.unlink()
                print(f"Error processing {doc_path}: {e}")
                skipped += 1
            
        return added, skipped
    
    def get_markdown_files(self):
        sources = self.rag_system.parent_store.list_sources()
        if sources:
            return sources
        return sorted(p.name for p in self.markdown_dir.glob("*.md"))
    
    def clear_all(self):
        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        self.rag_system.vector_db.delete_collection(self.rag_system.collection_name)

        clear_directory_contents(self.markdown_dir)
        self.rag_system.parent_store.clear_store()

        self.rag_system.vector_db.create_collection(self.rag_system.collection_name)
