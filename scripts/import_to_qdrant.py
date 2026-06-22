"""Import vector points JSON into Qdrant database.

Usage:
    # Using default config (http://localhost:6333)
    python scripts/import_to_qdrant.py

    # Custom config via environment variables
    QDRANT_URL=http://myhost:6333 python scripts/import_to_qdrant.py

    # Custom input file
    python scripts/import_to_qdrant.py output/content_analysis/vector_points.json

Requires: pip install qdrant-client
Requires: running Qdrant instance (docker run -d -p 6333:6333 qdrant/qdrant)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval.qdrant_config import QdrantConfig
from retrieval.vector_store import QdrantVectorStore


def main():
    points_path = sys.argv[1] if len(sys.argv) > 1 else "output/content_analysis/vector_points.json"

    if not Path(points_path).exists():
        print(f"ERROR: {points_path} not found. Run build_embeddings() first.")
        print("  python -m retrieval.embedder output/content_analysis/chunks.json output/content_analysis/vector_points.json")
        sys.exit(1)

    print(f"Importing: {points_path}")

    config = QdrantConfig.from_env()
    print(f"Qdrant URL:  {config.url}")
    print(f"Collection:  {config.collection_name}")

    store = QdrantVectorStore(config)
    if not store.connect():
        print("ERROR: Failed to connect to Qdrant. Is it running?")
        print("  docker run -d -p 6333:6333 qdrant/qdrant")
        sys.exit(1)

    count = store.load_from_json(points_path)
    print(f"\nImported {count} points into collection '{config.collection_name}'.")
    print("Done!")


if __name__ == "__main__":
    main()
