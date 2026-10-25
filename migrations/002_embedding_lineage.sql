-- ==============================================================================
-- DocChat Migration: 002_embedding_lineage.sql
-- Description: Embedding lineage tracking, chunk-level progress, and integrity
--              constraints. Safe to run once; the runner records the version.
-- ==============================================================================

-- Which model produced the vectors for a document. NULL means no successful
-- index yet. Queries are refused when this differs from the active model.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_dimension INT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunks_processed INT NOT NULL DEFAULT 0;

-- Re-embedding replaces a document's chunks atomically; a unique key makes
-- duplicate chunk indexes impossible if two ingests ever race.
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_chunks_document_chunk
    ON document_chunks(document_id, chunk_index);

-- Listing and status filters.
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

COMMENT ON COLUMN documents.embedding_model IS
    'Embedding model used for the active chunks; reindex when it differs from EMBEDDING_MODEL_NAME.';
COMMENT ON COLUMN documents.embedding_dimension IS
    'Vector dimension of the active chunks; must match the vector(N) column.';
