"""
conftest.py – Session-level stubs injected BEFORE any app module is imported.
=============================================================================
ROOT CAUSE of slow tests:
  1. `app.services.rag_memory` initializes ChromaDB + downloads ONNX embedding
     model on every import (~2-5s per test file).
  2. `app.agents.coach.agent` imports `google.genai` which also has init overhead.

FIX: Replace both heavy modules with lightweight MagicMock stubs at the
sys.modules level. pytest loads conftest.py first, so all subsequent imports
across every test file reuse these stubs — zero network, zero disk I/O.
"""

import sys
from unittest.mock import MagicMock

# ─────────────────────────────────────────────────────────────────────────────
# 1. Stub: google.genai (prevent real API client initialization)
# ─────────────────────────────────────────────────────────────────────────────
_google_stub = MagicMock()
sys.modules.setdefault("google", _google_stub)
sys.modules.setdefault("google.genai", _google_stub.genai)
sys.modules.setdefault("google.genai.types", _google_stub.genai.types)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Stub: chromadb (prevent ONNX model download + ChromaDB disk init)
# ─────────────────────────────────────────────────────────────────────────────
_chroma_stub = MagicMock()
sys.modules.setdefault("chromadb", _chroma_stub)
sys.modules.setdefault("chromadb.config", _chroma_stub.config)
sys.modules.setdefault("chromadb.utils", _chroma_stub.utils)
sys.modules.setdefault(
    "chromadb.utils.embedding_functions", _chroma_stub.utils.embedding_functions
)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Stub: rag_memory module itself — the singleton `rag_db` used across tools
#    and agent must be a MagicMock so tests can assert on .memorize() / .recall()
# ─────────────────────────────────────────────────────────────────────────────
_rag_memory_stub = MagicMock()
_rag_memory_stub.rag_db = MagicMock()
sys.modules["app.services.rag_memory"] = _rag_memory_stub
