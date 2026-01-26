from __future__ import annotations

# Single source of truth for embedding vector dimensionality used in Postgres (pgvector).
#
# pgvector requires a fixed dimension at the schema level: vector(d).
# If you change embedding models to a different dimension, you must run a migration.
EMBEDDING_DIMS = 1536


