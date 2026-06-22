"""BCM-RAG Content Analysis — Knowledge Graph Exporter.

Exports entities and relationships to Neo4j-compatible Cypher statements
and JSON format (for import via neo4j-admin or Python driver).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from content_analysis.models import Entity, EntityType, Relationship, RelType


class KnowledgeGraphExporter:
    """Export entities and relationships for Neo4j import."""

    def export_cypher(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
    ) -> str:
        """Generate Cypher CREATE statements."""
        lines = []
        lines.append("// BCM-RAG Knowledge Graph")
        lines.append(f"// Generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append(f"// Entities: {len(entities)}, Relationships: {len(relationships)}")
        lines.append("")

        # Create constraints
        lines.append("// Constraints")
        for etype in EntityType:
            lines.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{etype.value}) "
                f"REQUIRE n.entity_id IS UNIQUE;"
            )
        lines.append("")

        # Create entities
        lines.append("// Entities")
        for e in entities:
            props = self._format_props(e)
            lines.append(
                f"MERGE (n:{e.entity_type.value} {{entity_id: '{e.entity_id}'}}) "
                f"SET n = {props};"
            )
        lines.append("")

        # Create relationships
        lines.append("// Relationships")
        for r in relationships:
            props = "{weight: " + str(r.weight)
            if r.properties:
                for k, v in r.properties.items():
                    if isinstance(v, str):
                        props += f", {k}: '{v}'"
                    else:
                        props += f", {k}: {v}"
            props += "}"
            lines.append(
                f"MATCH (a {{entity_id: '{r.source_id}'}}) "
                f"MATCH (b {{entity_id: '{r.target_id}'}}) "
                f"MERGE (a)-[:{r.rel_type.value} {props}]->(b);"
            )

        return "\n".join(lines)

    def export_json(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
    ) -> dict:
        """Export as JSON for programmatic import."""
        return {
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "entity_type": e.entity_type.value,
                    "name": e.name,
                    "module": e.module,
                    "section_path": e.section_path,
                    "properties": e.properties,
                }
                for e in entities
            ],
            "relationships": [
                {
                    "source_id": r.source_id,
                    "target_id": r.target_id,
                    "rel_type": r.rel_type.value,
                    "properties": r.properties,
                    "weight": r.weight,
                }
                for r in relationships
            ],
        }

    def stats(self, entities: list[Entity], relationships: list[Relationship]) -> dict:
        """Compute statistics."""
        from collections import Counter
        return {
            "total_entities": len(entities),
            "entities_by_type": dict(Counter(e.entity_type.value for e in entities)),
            "entities_by_module": dict(Counter(e.module for e in entities if e.module)),
            "total_relationships": len(relationships),
            "relationships_by_type": dict(Counter(r.rel_type.value for r in relationships)),
        }

    @staticmethod
    def _format_props(e: Entity) -> str:
        """Format entity properties as Cypher map."""
        props = {
            "entity_id": e.entity_id,
            "name": e.name,
            "module": e.module,
            "section_path": e.section_path,
            "source_index": e.source_item_index,
        }
        props.update(e.properties)
        return json.dumps(props, ensure_ascii=False)
