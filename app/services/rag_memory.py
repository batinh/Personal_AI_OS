import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from chromadb.config import Settings
import chromadb
from chromadb.utils import embedding_functions

# 1. MANDATORY: Load .env BEFORE importing ChromaDB
load_dotenv()

# 2. FORCE CACHE REDIRECTION & DISABLE TELEMETRY IN OS.ENVIRON
if os.getenv("ANONYMIZED_TELEMETRY"):
    os.environ["ANONYMIZED_TELEMETRY"] = os.getenv("ANONYMIZED_TELEMETRY")

cache_dir = os.getenv("CHROMADB_CACHE_DIR")
if cache_dir:
    os.makedirs(cache_dir, exist_ok=True)
    # Force ChromaDB and HuggingFace (Embedding model) to save files in the mounted directory
    os.environ["CHROMA_CACHE_DIR"] = cache_dir
    os.environ["HF_HOME"] = cache_dir
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = cache_dir
    os.environ["XDG_CACHE_HOME"] = (
        cache_dir  # Add this to completely fix ONNX cache issues
    )
# 3. AFTER SETTING ENV VARS, FINALLY IMPORT CHROMA

from app.core.logging_conf import get_module_logger  # noqa: E402

logger = get_module_logger("memory")


class RagMemory:
    """
    Retrieval-Augmented Generation (RAG) Memory module.
    Uses Local AI Model to run 100% offline on the local server.
    """

    def __init__(self, db_path: str = "data/chroma_db"):
        # [ARCHITECTURE UPDATE] Disable Telemetry to protect Privacy and prevent junk logs
        self.client = chromadb.PersistentClient(
            path=db_path, settings=Settings(anonymized_telemetry=False)
        )
        # Activate Chroma's built-in Local AI Model (No Google API required)
        self.embed_fn = embedding_functions.DefaultEmbeddingFunction()

        # Create a new memory table (os_local_memory) compatible with the local model
        self.collection = self.client.get_or_create_collection(
            name="os_local_memory", embedding_function=self.embed_fn
        )
        logger.info(
            f"[RAG] Memory Center loaded using Local AI Embeddings at {db_path}"
        )

    def memorize(
        self,
        doc_id: str,
        content: str,
        domain: str,
        extra_meta: Optional[Dict[str, Any]] = None,
    ):
        """Store new memory into the vector database."""
        metadata = {"domain": domain}
        if extra_meta:
            metadata.update(extra_meta)

        self.collection.upsert(documents=[content], metadatas=[metadata], ids=[doc_id])
        logger.debug(f"[RAG] Successfully memorized item: {doc_id}")

    def recall(self, query: str, domain: Optional[str] = None, n_results: int = 5):
        """Recall memories based on the query."""
        where_clause = {"domain": domain} if domain else None

        results = self.collection.query(
            query_texts=[query], n_results=n_results, where=where_clause
        )
        return results

    def forget(self, doc_id: str):
        """Delete memory from ChromaDB so AI doesn't hallucinate deleted runs."""
        try:
            self.collection.delete(ids=[doc_id])
            logger.debug(f"[RAG] Successfully erased memory item: {doc_id}")
        except Exception:
            logger.warning(f"[RAG] Ignore delete memory {doc_id} (Maybe not exists).")


rag_db = RagMemory()
