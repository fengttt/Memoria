"""User-facing reflect & consolidate — sync with TTL cache."""

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from memoria.api.database import get_db_factory
from memoria.api.dependencies import get_current_user_id

router = APIRouter(tags=["memory"])

# In-memory TTL cache: (user_id, op) → (timestamp, result)
_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_TTL = {"consolidate": 1800, "reflect": 7200, "extract_entities": 3600}  # seconds


def _with_cache(user_id: str, op: str, fn, force: bool) -> dict:
    key = (user_id, op)
    now = time.time()
    if not force:
        cached = _cache.get(key)
        if cached:
            ts, result = cached
            remaining = _TTL[op] - (now - ts)
            if remaining > 0:
                return {**result, "cached": True, "cooldown_remaining_s": int(remaining)}
    result = fn()
    _cache[key] = (now, result)
    return result


@router.post("/consolidate")
def consolidate(
    force: bool = False,
    user_id: str = Depends(get_current_user_id),
    db_factory=Depends(get_db_factory),
):
    """Detect contradictions, fix orphaned nodes. 30min cooldown."""
    def _run():
        from memoria.core.memory.factory import create_memory_service
        svc = create_memory_service(db_factory, user_id=user_id)
        result = svc.consolidate(user_id)
        return result if isinstance(result, dict) else {"status": "done"}
    return _with_cache(user_id, "consolidate", _run, force)


@router.post("/reflect")
def reflect(
    force: bool = False,
    user_id: str = Depends(get_current_user_id),
    db_factory=Depends(get_db_factory),
):
    """Analyze memory clusters, synthesize insights. 2h cooldown. Requires LLM."""
    def _run():
        try:
            from memoria.core.memory.reflection.engine import ReflectionEngine
            from memoria.core.memory.tabular.candidates import CandidateProvider
            from memoria.core.memory.tabular.store import TabularStore

            store = TabularStore(db_factory)
            provider = CandidateProvider(db_factory)
            # LLM client — may not be configured
            from memoria.core.llm import get_llm_client
            llm = get_llm_client()
            engine = ReflectionEngine(provider, store, llm)
            result = engine.reflect(user_id)
            return {"insights": len(result.new_scenes), "skipped": result.skipped}
        except Exception as e:
            return {"insights": 0, "skipped": 0, "note": f"reflect unavailable: {e}"}
    return _with_cache(user_id, "reflect", _run, force)


@router.post("/extract-entities")
def extract_entities(
    force: bool = False,
    user_id: str = Depends(get_current_user_id),
    db_factory=Depends(get_db_factory),
):
    """LLM entity extraction for unlinked memories. Manual trigger only. 1h cooldown."""
    def _run():
        try:
            from memoria.core.memory.graph.service import GraphMemoryService
            from memoria.core.llm import get_llm_client
            llm = get_llm_client()
            svc = GraphMemoryService(db_factory)
            return svc.extract_entities_llm(user_id, llm)
        except Exception as e:
            return {"total_memories": 0, "entities_found": 0, "edges_created": 0, "error": str(e)}
    return _with_cache(user_id, "extract_entities", _run, force)


@router.post("/reflect/candidates")
def reflect_candidates(
    user_id: str = Depends(get_current_user_id),
    db_factory=Depends(get_db_factory),
):
    """Return raw reflection candidates for user-LLM synthesis (no internal LLM needed)."""
    from memoria.core.memory.graph.candidates import GraphCandidateProvider
    provider = GraphCandidateProvider(db_factory)
    candidates = provider.get_reflection_candidates(user_id)
    return {"candidates": [
        {
            "signal": c.signal,
            "importance": round(c.importance_score, 3),
            "memories": [{"memory_id": m.memory_id, "content": m.content, "type": str(m.memory_type)} for m in c.memories],
        }
        for c in candidates
    ]}


@router.post("/extract-entities/candidates")
def entity_candidates(
    user_id: str = Depends(get_current_user_id),
    db_factory=Depends(get_db_factory),
):
    """Return unlinked memories for user-LLM entity extraction."""
    from memoria.core.memory.graph.graph_store import GraphStore
    from memoria.core.memory.graph.types import EdgeType, NodeType
    store = GraphStore(db_factory)
    nodes = store.get_user_nodes(user_id, node_type=NodeType.SEMANTIC, active_only=True, load_embedding=False)
    if not nodes:
        return {"memories": []}
    node_ids = {n.node_id for n in nodes}
    edges = store.get_edges_for_nodes(node_ids)
    linked = {nid for nid, es in edges.items() if any(e.edge_type == EdgeType.ENTITY_LINK.value for e in es)}
    unlinked = [n for n in nodes if n.node_id not in linked]
    return {"memories": [{"memory_id": n.memory_id or n.node_id, "content": n.content} for n in unlinked[:50]]}


class LinkEntitiesRequest(BaseModel):
    entities: list[dict] = Field(..., min_length=1)


@router.post("/extract-entities/link")
def link_entities(
    req: LinkEntitiesRequest,
    user_id: str = Depends(get_current_user_id),
    db_factory=Depends(get_db_factory),
):
    """Write entity nodes + edges from user-LLM extraction results."""
    # Lazy imports: avoid loading graph subsystem at API startup
    from memoria.core.memory.graph.graph_store import GraphStore, _new_id
    from memoria.core.memory.graph.types import EdgeType, GraphNodeData, NodeType
    store = GraphStore(db_factory)
    entity_cache: dict[str, str] = {}
    pending_edges: list[tuple[str, str, str, float]] = []
    entities_created = 0
    for item in req.entities:
        memory_id = item.get("memory_id", "")
        node = store.get_node_by_memory_id(memory_id)
        if not node:
            continue
        for ent in item.get("entities", []):
            name = str(ent.get("name", "")).strip().lower()
            if not name:
                continue
            ent_node_id = entity_cache.get(name)
            if not ent_node_id:
                existing = store.find_entity_node(user_id, name)
                if existing:
                    ent_node_id = existing.node_id
                else:
                    ent_node_id = _new_id()
                    store.create_node(GraphNodeData(
                        node_id=ent_node_id, user_id=user_id,
                        node_type=NodeType.ENTITY, content=name,
                        confidence=1.0, trust_tier="T1", importance=0.4,
                    ))
                    entities_created += 1
                entity_cache[name] = ent_node_id
            pending_edges.append((node.node_id, ent_node_id, EdgeType.ENTITY_LINK.value, 1.0))
    if pending_edges:
        store.add_edges_batch(pending_edges, user_id)
    return {"entities_created": entities_created, "edges_created": len(pending_edges)}
