"""Knowledge-map node/edge store.

A saved kb_entry becomes a concept node (one per entry). Positions are
persisted so a drag sticks. Edges are either 'manual' (the user drew them) or
'semantic' (the user accepted a vector-similarity suggestion).
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from .db import utcnow

_TONES = ["spark", "sky", "grass", "berry", "sun", "rose"]


def _layout_position(index: int) -> Dict[str, float]:
    """A light deterministic spiral layout for a new node."""
    import math
    # golden-angle spiral keeps early nodes spread without overlap.
    angle = index * 2.399963229728653  # ~137.5°
    radius = 8 + 5.2 * math.sqrt(index)
    radius = min(radius, 42)
    x = 50 + radius * math.cos(angle)
    y = 50 + radius * math.sin(angle)
    return {"x": round(max(6, min(94, x)), 2), "y": round(max(6, min(94, y)), 2)}


def ensure_concept_for_entry(conn: sqlite3.Connection, kb_entry_id: int) -> int:
    """Create the concept node for a kb_entry if absent. Returns concept id."""
    existing = conn.execute(
        "SELECT id FROM concept WHERE kb_entry_id=?", (kb_entry_id,)
    ).fetchone()
    if existing:
        return int(existing["id"])
    entry = conn.execute(
        "SELECT term FROM kb_entry WHERE id=?", (kb_entry_id,)
    ).fetchone()
    label = (entry["term"] if entry else None) or "Concept"
    count = conn.execute("SELECT COUNT(*) AS c FROM concept").fetchone()["c"]
    pos = _layout_position(count)
    tone = _TONES[count % len(_TONES)]
    cur = conn.execute(
        "INSERT INTO concept (label, kb_entry_id, x, y, tone, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (label, kb_entry_id, pos["x"], pos["y"], tone, utcnow()),
    )
    conn.commit()
    return int(cur.lastrowid)


def refresh_from_entries(conn: sqlite3.Connection) -> int:
    """Ensure every kb_entry has a concept node. Returns nodes created."""
    rows = conn.execute(
        "SELECT id FROM kb_entry WHERE id NOT IN (SELECT kb_entry_id FROM concept "
        "WHERE kb_entry_id IS NOT NULL) AND (mode IS NULL OR mode != 'chat') "
        "ORDER BY id"
    ).fetchall()
    created = 0
    for r in rows:
        ensure_concept_for_entry(conn, int(r["id"]))
        created += 1
    return created


def get_map(conn: sqlite3.Connection) -> Dict[str, List[dict]]:
    """Return {nodes:[...], edges:[...]}, refreshing nodes from entries first."""
    refresh_from_entries(conn)
    nodes = []
    for r in conn.execute(
        "SELECT id, label, kb_entry_id, x, y, tone FROM concept ORDER BY id"
    ).fetchall():
        nodes.append({
            "id": int(r["id"]),
            "label": r["label"],
            "kb_entry_id": r["kb_entry_id"],
            "x": r["x"],
            "y": r["y"],
            "tone": r["tone"],
        })
    edges = []
    for r in conn.execute(
        "SELECT id, src_concept_id, dst_concept_id, source FROM concept_edge ORDER BY id"
    ).fetchall():
        edges.append({
            "id": int(r["id"]),
            "src": int(r["src_concept_id"]),
            "dst": int(r["dst_concept_id"]),
            "source": r["source"],
        })
    return {"nodes": nodes, "edges": edges}


def set_position(conn: sqlite3.Connection, concept_id: int, x: float, y: float) -> None:
    x = max(0.0, min(100.0, float(x)))
    y = max(0.0, min(100.0, float(y)))
    conn.execute("UPDATE concept SET x=?, y=? WHERE id=?", (x, y, concept_id))
    conn.commit()


def add_edge(conn: sqlite3.Connection, src: int, dst: int, source: str = "manual") -> Optional[int]:
    """Create an edge (deduped, undirected-ish). Returns id or None if dupe/self."""
    if src == dst:
        return None
    a, b = (src, dst)
    # Normalize so (a,b) and (b,a) collapse to one row.
    if a > b:
        a, b = b, a
    existing = conn.execute(
        "SELECT id FROM concept_edge WHERE src_concept_id=? AND dst_concept_id=?",
        (a, b),
    ).fetchone()
    if existing:
        return int(existing["id"])
    # Guard: both concepts must exist.
    for cid in (a, b):
        if conn.execute("SELECT 1 FROM concept WHERE id=?", (cid,)).fetchone() is None:
            return None
    cur = conn.execute(
        "INSERT INTO concept_edge (src_concept_id, dst_concept_id, source, created_at) "
        "VALUES (?,?,?,?)",
        (a, b, source, utcnow()),
    )
    conn.commit()
    return int(cur.lastrowid)


def delete_edge(conn: sqlite3.Connection, edge_id: int) -> None:
    conn.execute("DELETE FROM concept_edge WHERE id=?", (edge_id,))
    conn.commit()
