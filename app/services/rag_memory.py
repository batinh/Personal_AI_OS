import os
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from chromadb.config import Settings
import chromadb
from chromadb.utils import embedding_functions

# 1. BẮT BUỘC: Nạp file .env TRƯỚC khi import ChromaDB
load_dotenv()

# 2. ÉP BUỘC CHUYỂN HƯỚNG CACHE & TẮT TELEMETRY VÀO OS.ENVIRON
if os.getenv("ANONYMIZED_TELEMETRY"):
    os.environ["ANONYMIZED_TELEMETRY"] = os.getenv("ANONYMIZED_TELEMETRY")

cache_dir = os.getenv("CHROMADB_CACHE_DIR")
if cache_dir:
    os.makedirs(cache_dir, exist_ok=True)
    # Ép ChromaDB và HuggingFace (Model nhúng) lưu file vào thư mục được mount
    os.environ["CHROMA_CACHE_DIR"] = cache_dir
    os.environ["HF_HOME"] = cache_dir
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = cache_dir
    os.environ["XDG_CACHE_HOME"] = cache_dir # Thêm dòng này để trị triệt để ONNX
# 3. SAU KHI ĐÃ GÀI BIẾN MÔI TRƯỜNG XONG, MỚI IMPORT CHROMA

logger = logging.getLogger("AI_COACH")

class RagMemory:
    """
    Retrieval-Augmented Generation (RAG) Memory module.
    Sử dụng Local AI Model để chạy 100% offline trên máy chủ cục bộ.
    """
    def __init__(self, db_path: str = "data/chroma_db"):
        # [CẬP NHẬT KIẾN TRÚC] Tắt Telemetry để bảo vệ Privacy và chặn lỗi log rác
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(anonymized_telemetry=False)
        )
        # Kích hoạt Local AI Model tích hợp sẵn của Chroma (Không cần Google API)
        self.embed_fn = embedding_functions.DefaultEmbeddingFunction()
        
        # Tạo bảng bộ nhớ mới (os_local_memory) để tương thích với model cục bộ
        self.collection = self.client.get_or_create_collection(
            name="os_local_memory",
            embedding_function=self.embed_fn
        )
        logger.info(f"[RAG] Memory Center loaded using Local AI Embeddings at {db_path}")

    def memorize(self, doc_id: str, content: str, domain: str, extra_meta: Optional[Dict[str, Any]] = None):
        """Lưu trữ ký ức mới vào vector database."""
        metadata = {"domain": domain}
        if extra_meta:
            metadata.update(extra_meta)
            
        self.collection.upsert(
            documents=[content],
            metadatas=[metadata],
            ids=[doc_id]
        )
        logger.debug(f"[RAG] Successfully memorized item: {doc_id}")

    def recall(self, query: str, domain: Optional[str] = None, n_results: int = 5):
        """Hồi tưởng ký ức dựa trên câu hỏi."""
        where_clause = {"domain": domain} if domain else None
        
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_clause
        )
        return results
    def forget(self, doc_id: str):
        """Xóa ký ức khỏi ChromaDB để AI không nhớ nhầm bài chạy đã xóa."""
        try:
            self.collection.delete(ids=[doc_id])
            logger.debug(f"[RAG] Successfully erased memory item: {doc_id}")
        except Exception as e:
            logger.warning(f"[RAG] Ignore delete memory {doc_id} (Maybe not exists).")
rag_db = RagMemory()