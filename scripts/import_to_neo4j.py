"""Import knowledge graph JSON into Neo4j database.

Usage:
    # Using default config (bolt://localhost:7687, neo4j/password)
    python scripts/import_to_neo4j.py

    # Custom config via environment variables
    NEO4J_URI=bolt://myhost:7687 NEO4J_USER=admin NEO4J_PASSWORD=secret \
        python scripts/import_to_neo4j.py

    # Custom input file
    python scripts/import_to_neo4j.py output/content_analysis/knowledge_graph.json

Requires: pip install neo4j
Requires: running Neo4j instance (docker run -d -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval.neo4j_config import Neo4jConfig
from retrieval.graph_store import Neo4jGraphStore


def main():
    kg_path = sys.argv[1] if len(sys.argv) > 1 else "output/content_analysis/knowledge_graph.json"

    if not Path(kg_path).exists():
        print(f"ERROR: {kg_path} not found. Run content analysis pipeline first.")
        sys.exit(1)

    print(f"Importing: {kg_path}")

    config = Neo4jConfig.from_env()
    print(f"Neo4j URI: {config.uri}")
    print(f"User:      {config.user}")
    print(f"Database:  {config.database}")

    store = Neo4jGraphStore(config)
    if not store.connect():
        print("ERROR: Failed to connect to Neo4j. Is it running?")
        print("  docker run -d -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5")
        sys.exit(1)

    count = store.load_from_json(kg_path)
    print(f"\nImported {count} nodes.")
    print("Done!")


if __name__ == "__main__":
    main()
