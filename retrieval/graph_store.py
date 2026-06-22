"""BCM-RAG — Graph Store abstraction layer.

Provides pluggable backends for knowledge graph storage and traversal:
  - NetworkXGraphStore: in-memory (default, zero-dependency)
  - Neo4jGraphStore:   persistent, production-grade

Usage:
    store = create_graph_store()         # auto-detect from env
    store = NetworkXGraphStore()         # explicit in-memory
    store = Neo4jGraphStore(config)      # explicit Neo4j
    store.connect()
    store.load_from_json("knowledge_graph.json")
    results = store.expand(entity_id, hops=2)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Abstract Base
# ---------------------------------------------------------------------------

class BaseGraphStore(ABC):
    """Abstract interface for graph storage and traversal."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def load_from_json(self, path: str | Path) -> int:
        """Load entities and relationships from knowledge_graph.json.
        Returns number of nodes loaded.
        """
        ...

    @abstractmethod
    def search_entities(self, query: str) -> list[dict]:
        """Substring search across entity names. Returns [{entity_id, name, type, module}, ...]."""
        ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> dict | None:
        """Get entity by ID. Returns {entity_id, name, type, module, properties} or None."""
        ...

    @abstractmethod
    def get_by_name(self, name: str, entity_type: str | None = None) -> list[dict]:
        """Get entities by exact name match."""
        ...

    @abstractmethod
    def expand(self, entity_ids: list[str], hops: int = 1) -> list[dict]:
        """BFS expansion from seed entities. Returns [{entity, distance, relation}, ...]."""
        ...

    @abstractmethod
    def trace_dependency_chain(
        self, entity_id: str, max_depth: int = 5,
    ) -> list[dict]:
        """Trace dependency chain (DEPENDS_ON, REQUIRES, TRIGGERED_BY, CONTROLS)."""
        ...

    @abstractmethod
    def get_subgraph(self, entity_ids: list[str]) -> dict:
        """Extract subgraph containing given entities. Returns {nodes, edges}."""
        ...

    @property
    @abstractmethod
    def stats(self) -> dict:
        """Return {nodes, edges} counts."""
        ...


# ---------------------------------------------------------------------------
# NetworkX Backend (in-memory, default)
# ---------------------------------------------------------------------------

class NetworkXGraphStore(BaseGraphStore):
    """In-memory graph store backed by NetworkX DiGraph.

    This is the default backend — zero external dependencies beyond networkx.
    Suitable for development, testing, and single-document deployments.
    """

    def __init__(self):
        import networkx as nx
        self.graph = nx.DiGraph()
        self.entity_index: dict[str, dict] = {}
        self.name_index: dict[str, list[str]] = defaultdict(list)
        self._node_count = 0
        self._edge_count = 0

    # ---- Connection (no-op for in-memory) ----

    def connect(self) -> bool:
        return True  # always available

    # ---- Load ----

    def load_from_json(self, path: str | Path) -> int:
        """Load from knowledge_graph.json."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        entities = data.get("entities", data.get("nodes", []))
        relationships = data.get("relationships", data.get("edges", []))

        for ent in entities:
            eid = ent["entity_id"]
            self.graph.add_node(eid, **ent)
            self.entity_index[eid] = ent
            name = ent.get("name", "")
            if name:
                self.name_index[name.lower()].append(eid)
            self._node_count += 1

        for rel in relationships:
            src = rel.get("source_id", rel.get("source", ""))
            tgt = rel.get("target_id", rel.get("target", ""))
            if src and tgt:
                self.graph.add_edge(src, tgt, **rel)
                self._edge_count += 1

        return self._node_count

    # ---- Search ----

    def search_entities(self, query: str) -> list[dict]:
        q = query.lower()
        results = []
        for eid, ent in self.entity_index.items():
            name = ent.get("name", "").lower()
            if q in name or q in eid.lower():
                results.append({
                    "entity_id": eid,
                    "name": ent.get("name", ""),
                    "entity_type": ent.get("entity_type", ""),
                    "module": ent.get("module", ""),
                })
        return results

    def get_entity(self, entity_id: str) -> dict | None:
        return self.entity_index.get(entity_id)

    def get_by_name(self, name: str, entity_type: str | None = None) -> list[dict]:
        eids = self.name_index.get(name.lower(), [])
        results = []
        for eid in eids:
            ent = self.entity_index.get(eid)
            if ent:
                if entity_type and ent.get("entity_type") != entity_type:
                    continue
                results.append(ent)
        return results

    # ---- Traversal ----

    def expand(self, entity_ids: list[str], hops: int = 1) -> list[dict]:
        """BFS expansion from seed entities up to `hops` steps."""
        import networkx as nx

        results: list[dict] = []
        visited: set[str] = set(entity_ids)

        frontier = set(entity_ids)
        for depth in range(1, hops + 1):
            next_frontier: set[str] = set()
            for node in list(frontier):
                if node not in self.graph:
                    continue
                # Outgoing edges
                for _, neighbor in self.graph.out_edges(node):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
                        edge_data = self.graph.get_edge_data(node, neighbor) or {}
                        ent = self.entity_index.get(neighbor, {})
                        results.append({
                            "entity": {
                                "entity_id": neighbor,
                                "name": ent.get("name", ""),
                                "entity_type": ent.get("entity_type", ""),
                                "module": ent.get("module", ""),
                            },
                            "distance": depth,
                            "relation": edge_data.get("rel_type", ""),
                        })
                # Incoming edges
                for neighbor, _ in self.graph.in_edges(node):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
                        edge_data = self.graph.get_edge_data(neighbor, node) or {}
                        ent = self.entity_index.get(neighbor, {})
                        results.append({
                            "entity": {
                                "entity_id": neighbor,
                                "name": ent.get("name", ""),
                                "entity_type": ent.get("entity_type", ""),
                                "module": ent.get("module", ""),
                            },
                            "distance": depth,
                            "relation": edge_data.get("rel_type", ""),
                        })
            frontier = next_frontier
            if not frontier:
                break

        return results

    def trace_dependency_chain(
        self, entity_id: str, max_depth: int = 5,
    ) -> list[dict]:
        """DFS-based dependency chain tracing."""
        dep_types = {"depends_on", "requires", "triggered_by", "controls"}
        results: list[dict] = []
        visited: set[str] = {entity_id}

        def _dfs(node: str, depth: int, path: list[str]):
            if depth > max_depth or node not in self.graph:
                return
            for _, neighbor in self.graph.out_edges(node):
                edge_data = self.graph.get_edge_data(node, neighbor) or {}
                rel_type = edge_data.get("rel_type", "")
                if rel_type in dep_types and neighbor not in visited:
                    visited.add(neighbor)
                    ent = self.entity_index.get(neighbor, {})
                    new_path = path + [neighbor]
                    results.append({
                        "entity": {
                            "entity_id": neighbor,
                            "name": ent.get("name", ""),
                            "entity_type": ent.get("entity_type", ""),
                            "module": ent.get("module", ""),
                        },
                        "depth": depth,
                        "path": new_path,
                        "relation": rel_type,
                    })
                    _dfs(neighbor, depth + 1, new_path)

        _dfs(entity_id, 1, [entity_id])
        return results

    def get_subgraph(self, entity_ids: list[str]) -> dict:
        """Extract subgraph containing given entities."""
        eid_set = set(entity_ids)
        nodes = []
        edges = []
        for eid in eid_set:
            if eid in self.entity_index:
                nodes.append(self.entity_index[eid])

        for src, tgt, data in self.graph.edges(data=True):
            if src in eid_set or tgt in eid_set:
                edges.append({
                    "source_id": src,
                    "target_id": tgt,
                    **data,
                })

        return {"nodes": nodes, "edges": edges}

    @property
    def stats(self) -> dict:
        return {"nodes": self._node_count, "edges": self._edge_count}


# ---------------------------------------------------------------------------
# Neo4j Backend (persistent, production)
# ---------------------------------------------------------------------------

class Neo4jGraphStore(BaseGraphStore):
    """Neo4j-backed graph store for production deployment.

    Requires: pip install neo4j
    Requires: running Neo4j instance (docker or cloud)
    """

    def __init__(self, config=None):
        from retrieval.neo4j_config import Neo4jConfig
        self.config = config or Neo4jConfig.from_env()
        self._driver = None
        self._node_count = 0
        self._edge_count = 0

    def connect(self) -> bool:
        """Connect to Neo4j database. Returns True on success."""
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
                max_connection_lifetime=self.config.max_connection_lifetime,
                max_connection_pool_size=self.config.max_connection_pool_size,
                connection_acquisition_timeout=self.config.connection_acquisition_timeout,
            )
            # Verify connection
            self._driver.verify_connectivity()
            print(f"Neo4jGraphStore: connected to {self.config.uri}")
            return True
        except ImportError:
            print("Neo4jGraphStore: neo4j package not installed. Run: pip install neo4j")
            return False
        except Exception as e:
            print(f"Neo4jGraphStore: connection failed ({e})")
            return False

    def load_from_json(self, path: str | Path) -> int:
        """Load from knowledge_graph.json into Neo4j."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not self._driver:
            if not self.connect():
                return 0

        entities = data.get("entities", data.get("nodes", []))
        relationships = data.get("relationships", data.get("edges", []))

        with self._driver.session(database=self.config.database) as session:
            # Create constraints (idempotent)
            for etype in self._distinct_types(entities):
                try:
                    session.run(
                        f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) "
                        f"REQUIRE n.entity_id IS UNIQUE"
                    )
                except Exception:
                    pass  # constraint may already exist

            # Batch create nodes
            for ent in entities:
                session.run(
                    "MERGE (n:Entity {entity_id: $eid}) "
                    "SET n.name = $name, n.entity_type = $etype, n.module = $mod, "
                    "n.section_path = $spath, n += $props",
                    eid=ent["entity_id"],
                    name=ent.get("name", ""),
                    etype=ent.get("entity_type", ""),
                    mod=ent.get("module", ""),
                    spath=ent.get("section_path", ""),
                    props=ent.get("properties", {}),
                )
                self._node_count += 1

            # Batch create relationships
            for rel in relationships:
                src = rel.get("source_id", rel.get("source", ""))
                tgt = rel.get("target_id", rel.get("target", ""))
                rtype = rel.get("rel_type", "").upper()
                if not src or not tgt or not rtype:
                    continue
                session.run(
                    f"MATCH (a:Entity {{entity_id: $src}}) "
                    f"MATCH (b:Entity {{entity_id: $tgt}}) "
                    f"MERGE (a)-[r:{rtype}]->(b) "
                    f"SET r.weight = $weight, r += $props",
                    src=src, tgt=tgt,
                    weight=rel.get("weight", 1.0),
                    props=rel.get("properties", {}),
                )
                self._edge_count += 1

        print(f"Neo4jGraphStore: loaded {self._node_count} nodes, {self._edge_count} edges")
        return self._node_count

    def search_entities(self, query: str) -> list[dict]:
        if not self._driver:
            return []
        with self._driver.session(database=self.config.database) as session:
            result = session.run(
                "MATCH (n:Entity) WHERE n.name CONTAINS $q OR n.entity_id CONTAINS $q "
                "RETURN n.entity_id AS entity_id, n.name AS name, "
                "n.entity_type AS entity_type, n.module AS module "
                "LIMIT 50",
                q=query,
            )
            return [dict(record) for record in result]

    def get_entity(self, entity_id: str) -> dict | None:
        if not self._driver:
            return None
        with self._driver.session(database=self.config.database) as session:
            result = session.run(
                "MATCH (n:Entity {entity_id: $eid}) RETURN n",
                eid=entity_id,
            )
            record = result.single()
            return dict(record["n"]) if record else None

    def get_by_name(self, name: str, entity_type: str | None = None) -> list[dict]:
        if not self._driver:
            return []
        query = "MATCH (n:Entity) WHERE n.name = $name"
        if entity_type:
            query += " AND n.entity_type = $etype"
        query += " RETURN n"
        params = {"name": name}
        if entity_type:
            params["etype"] = entity_type
        with self._driver.session(database=self.config.database) as session:
            result = session.run(query, **params)
            return [dict(record["n"]) for record in result]

    def expand(self, entity_ids: list[str], hops: int = 1) -> list[dict]:
        if not self._driver:
            return []
        results = []
        with self._driver.session(database=self.config.database) as session:
            for eid in entity_ids:
                result = session.run(
                    f"MATCH (n:Entity {{entity_id: $eid}})-[r]-(m:Entity) "
                    f"WHERE n.entity_id <> m.entity_id "
                    f"RETURN m.entity_id AS entity_id, m.name AS name, "
                    f"m.entity_type AS entity_type, m.module AS module, "
                    f"type(r) AS relation, 1 AS distance "
                    f"LIMIT 100",
                    eid=eid,
                )
                for record in result:
                    d = dict(record)
                    results.append({
                        "entity": {
                            "entity_id": d["entity_id"],
                            "name": d.get("name", ""),
                            "entity_type": d.get("entity_type", ""),
                            "module": d.get("module", ""),
                        },
                        "distance": d["distance"],
                        "relation": d.get("relation", ""),
                    })
        return results

    def trace_dependency_chain(
        self, entity_id: str, max_depth: int = 5,
    ) -> list[dict]:
        if not self._driver:
            return []
        dep_types = ["DEPENDS_ON", "REQUIRES", "TRIGGERED_BY", "CONTROLS"]
        rel_pattern = "|".join(dep_types)
        results = []
        with self._driver.session(database=self.config.database) as session:
            result = session.run(
                f"MATCH path = (n:Entity {{entity_id: $eid}})"
                f"-[:{rel_pattern}*1..{max_depth}]->(m:Entity) "
                f"RETURN nodes(path) AS nodes, relationships(path) AS rels",
                eid=entity_id,
            )
            for record in result:
                path_nodes = record["nodes"]
                path_rels = record["rels"]
                for i, node in enumerate(path_nodes[1:], 1):
                    results.append({
                        "entity": {
                            "entity_id": node.get("entity_id", ""),
                            "name": node.get("name", ""),
                            "entity_type": node.get("entity_type", ""),
                            "module": node.get("module", ""),
                        },
                        "depth": i,
                        "path": [n.get("entity_id", "") for n in path_nodes[:i + 1]],
                        "relation": type(path_rels[i - 1]).__name__ if i <= len(path_rels) else "",
                    })
        return results

    def get_subgraph(self, entity_ids: list[str]) -> dict:
        if not self._driver:
            return {"nodes": [], "edges": []}
        with self._driver.session(database=self.config.database) as session:
            nodes_result = session.run(
                "MATCH (n:Entity) WHERE n.entity_id IN $ids RETURN n",
                ids=entity_ids,
            )
            nodes = [dict(record["n"]) for record in nodes_result]
            edges_result = session.run(
                "MATCH (a:Entity)-[r]->(b:Entity) "
                "WHERE a.entity_id IN $ids OR b.entity_id IN $ids "
                "RETURN a.entity_id AS source_id, b.entity_id AS target_id, "
                "type(r) AS rel_type, r.weight AS weight",
                ids=entity_ids,
            )
            edges = [dict(record) for record in edges_result]
            return {"nodes": nodes, "edges": edges}

    @property
    def stats(self) -> dict:
        if not self._driver:
            return {"nodes": 0, "edges": 0}
        with self._driver.session(database=self.config.database) as session:
            nodes = session.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]
            edges = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _distinct_types(entities: list[dict]) -> list[str]:
        types = set()
        for ent in entities:
            t = ent.get("entity_type", "")
            if t:
                types.add(t)
        return list(types)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_graph_store() -> BaseGraphStore:
    """Create graph store backend based on environment configuration.

    Set BCM_USE_NEO4J=1 to use Neo4j, otherwise defaults to NetworkX.
    """
    from retrieval.neo4j_config import Neo4jConfig

    config = Neo4jConfig.from_env()
    if config.enabled:
        store = Neo4jGraphStore(config)
        if store.connect():
            return store
        print("WARNING: Neo4j connection failed, falling back to NetworkX")

    return NetworkXGraphStore()
