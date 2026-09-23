"""Version-exact impact model: change events, affected sets, impact records.

The rules implemented here are frozen in ``docs/067/A0_RULES.md``; the expected
outputs are in ``docs/067/A0_COUNTEREXAMPLES.md``. Four of them are easy to get
subtly wrong and are called out at their implementation site: reverse traversal
must pass through historical versions (R10), the idempotency key must carry the
change id (R13), the affected set always contains its own root (R11), and the
propagation basis is the unified view of both reference fields (R2).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .errors import ValidationError
from .memory_store import is_knowledge_ref, parse_knowledge_ref


SCOPE_MODES = ("versions", "none", "unknown")
CHANGE_KINDS = ("retract", "correct", "narrow", "reword")


def _impact_id(change_id: str, affected_version: str) -> str:
    """Stable id derived from the idempotency key, so a replay of the same
    change re-derives the same row rather than inserting a duplicate."""
    digest = hashlib.sha256(f"{change_id}:{affected_version}".encode()).hexdigest()
    return "IM-" + digest[:20]


def validate_change(
    *,
    mode: object,
    scope: object,
    kind: object,
    knowledge_id: str,
    new_revision: int,
    retracts: bool,
) -> tuple[str, list[str], str]:
    """Check one change declaration and return ``(mode, scope, kind)``.

    ``mode`` has no default. All three values are substantive claims by the
    reviser and the program must not pick one for them: ``none`` would silently
    assert that nothing was invalidated, and ``unknown`` would spread every
    prior version of the entry across the candidate roots on each reword.
    """
    if mode is None:
        raise ValidationError(
            "affected_scope_mode is required for a revision; one of " + ", ".join(SCOPE_MODES)
        )
    if mode not in SCOPE_MODES:
        raise ValidationError(f"affected_scope_mode must be one of {', '.join(SCOPE_MODES)}")

    kind = kind or "correct"
    if kind not in CHANGE_KINDS:
        raise ValidationError(f"change_kind must be one of {', '.join(CHANGE_KINDS)}")

    # R7: the only structural contradiction the program judges. Scientific
    # correctness of the declared scope is the reviser's, not ours.
    # The test is the transition into ``retracted``, not the resulting status:
    # a later wording fix to an already-retracted entry is an ordinary revision
    # and must not be forced to re-declare a retraction.
    if retracts:
        if mode == "none":
            raise ValidationError("A retraction cannot declare affected_scope_mode='none'")
        if kind != "retract":
            raise ValidationError("A revision that retracts must use change_kind='retract'")

    if mode != "versions":
        if scope:
            raise ValidationError(f"affected_scope must be empty when mode is {mode!r}")
        return mode, [], kind

    if not isinstance(scope, list) or not scope:
        raise ValidationError("affected_scope must be a nonempty list when mode is 'versions'")
    resolved: list[str] = []
    for item in scope:
        if not isinstance(item, str):
            raise ValidationError("affected_scope entries must be exact version references")
        entry, revision = parse_knowledge_ref(item)
        if entry != knowledge_id:
            raise ValidationError(
                f"affected_scope may only name versions of {knowledge_id}, found {item}"
            )
        if revision >= new_revision:
            raise ValidationError(f"affected_scope may only name versions that already exist: {item}")
        if item not in resolved:
            resolved.append(item)
    return mode, resolved, kind


def candidate_roots(
    db: sqlite3.Connection, knowledge_id: str, mode: str, scope: list[str], new_revision: int
) -> list[str]:
    """R8. ``unknown`` means every version of this entry that existed before the
    change, and nothing else: not future versions, not other entries."""
    if mode == "none":
        return []
    if mode == "versions":
        return list(scope)
    rows = db.execute(
        "SELECT revision FROM knowledge_revisions WHERE knowledge_id=? AND revision<? ORDER BY revision",
        (knowledge_id, new_revision),
    ).fetchall()
    return [f"knowledge/{knowledge_id}@{row[0]}" for row in rows]


def _citers(db: sqlite3.Connection, used_ref: str) -> list[tuple[str, int, int]]:
    return [
        (row[0], row[1], row[2])
        for row in db.execute(
            "SELECT user_ref,from_dependencies,from_evidence"
            " FROM knowledge_support_edges WHERE used_ref=?",
            (used_ref,),
        )
    ]


def _edge_source(from_dependencies: int, from_evidence: int) -> str:
    if from_dependencies and from_evidence:
        return "both"
    return "dependencies" if from_dependencies else "evidence_refs"


def affected_set(db: sqlite3.Connection, roots: list[str]) -> dict[str, tuple[int, str]]:
    """Reverse-reachable versions, mapped to ``(hop, edge_source)``.

    Two details carry the counterexamples. The roots are in the result at hop 0
    even when nothing cites them (R11), because a disposition needs a key to
    hang on and a publication may pin the root directly. And traversal walks
    the version graph, never an entry's latest revision only (R10): a use that
    is still pinned to an old version is exactly what must not be missed.

    ``edge_source`` describes the edge leaving the affected version, so a row
    explains how *it* reaches the root.
    """
    result: dict[str, tuple[int, str]] = {}
    seen: set = set()
    frontier: list[str] = []
    for root in roots:
        if root not in result:
            # Roots are in the set whatever their status (R11): a disposition
            # needs a key, and a publication may pin the root directly.
            result[root] = (0, "root")
            seen.add(root)
            frontier.append(root)
    hop = 0
    while frontier:
        hop += 1
        following: list[str] = []
        for used_ref in frontier:
            for user_ref, from_dependencies, from_evidence in _citers(db, used_ref):
                if user_ref in seen:
                    continue  # breadth-first, so the first arrival is the shortest
                seen.add(user_ref)
                following.append(user_ref)
                row = _revision_row(db, user_ref)
                if row is not None and row["status"] == "retracted":
                    # A retracted version asserts nothing, so it is not an
                    # action item (R10, 2026-09-21). Traversal still passes
                    # through it: the edges it inherited are real edges, and
                    # whoever cites *it* must still be found.
                    continue
                result[user_ref] = (hop, _edge_source(from_dependencies, from_evidence))
        frontier = following
    return result


def record_impacts(
    db: sqlite3.Connection,
    change_id: str,
    targets: dict[str, tuple[int, str]],
    detected_at: str,
    sequence: int,
) -> int:
    """Materialise impact rows under the ``(change_id, affected_version)`` key.

    Not ``INSERT OR IGNORE``: a late reference can produce a shorter path to a
    target that is already recorded, and ignoring the write would keep the
    stale larger hop. Everything else about an existing row is left alone, so
    review progress and dispositions survive a replay (CE-1).
    """
    for version, (hop, edge_source) in targets.items():
        db.execute(
            "INSERT INTO knowledge_impacts"
            " (impact_id,change_id,affected_version,hop,edge_source,review_state,"
            "  disposition_ref,voided_by_scope_revision,detected_at,detected_sequence)"
            " VALUES (?,?,?,?,?,'pending',NULL,NULL,?,?)"
            " ON CONFLICT(change_id,affected_version) DO UPDATE SET"
            # edge_source first: on the right-hand side ``hop`` still refers to
            # the stored value, so the two columns cannot disagree about which
            # path they describe.
            "  edge_source=CASE WHEN excluded.hop < hop THEN excluded.edge_source"
            "                   ELSE edge_source END,"
            "  hop=MIN(hop,excluded.hop)",
            (
                _impact_id(change_id, version),
                change_id,
                version,
                hop,
                edge_source,
                detected_at,
                sequence,
            ),
        )
    return len(targets)


def apply_change(
    db: sqlite3.Connection,
    *,
    change_id: str,
    knowledge_id: str,
    new_revision: int,
    kind: str,
    mode: str,
    scope: list[str],
    reason: str,
    operation_id: str,
    created_at: str,
    sequence: int,
) -> dict:
    """Write the change event and its impact records in the caller's transaction."""
    db.execute(
        "INSERT INTO knowledge_changes"
        " (change_id,knowledge_id,new_revision,kind,affected_scope_mode,affected_scope,"
        "  reason,operation_id,created_at,source_sequence)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            change_id,
            knowledge_id,
            new_revision,
            kind,
            mode,
            json.dumps(scope, ensure_ascii=False),
            reason,
            operation_id,
            created_at,
            sequence,
        ),
    )
    roots = candidate_roots(db, knowledge_id, mode, scope, new_revision)
    targets = affected_set(db, roots)
    record_impacts(db, change_id, targets, created_at, sequence)
    return {
        "change_id": change_id,
        "kind": kind,
        "affected_scope_mode": mode,
        "affected_scope": scope,
        "roots": roots,
        "impact_count": len(targets),
    }


def late_reference_impacts(
    db: sqlite3.Connection,
    version_ref: str,
    detected_at: str,
    sequence: int,
) -> list[str]:
    """R12. A reference written after a change still has to be found.

    The test is reachability to any impact root of an existing change, not
    membership of the new basis in some scope set. Membership alone misses the
    indirect case where the newly cited version is itself only a step away from
    the root (CE-9). The original ``change_id`` is reused: registering a
    reference is not itself a change of basis.
    """
    # Roots come from each change's *currently effective* scope, never from the
    # materialised hop-0 rows. Those rows survive a narrowing on purpose (R9),
    # so reading them here would re-attach a late reference to a root the
    # reviser has already excluded (CE-8e step 4).
    roots = [
        (change_id, root)
        for (change_id,) in db.execute("SELECT change_id FROM knowledge_changes")
        for root in change_roots(db, change_id)
    ]
    if not roots:
        return []
    row = _revision_row(db, version_ref)
    if row is not None and row["status"] == "retracted":
        # A retracted version no longer asserts anything, so there is nothing
        # to re-examine and no impact record to raise (R10, 2026-09-21).
        return []
    # One forward walk from the new version, rather than re-deriving every
    # change's affected set. Sound because an edge is only ever written by the
    # version that declares it, and old revisions are never updated (R3), so no
    # pre-existing version can gain an outgoing edge and change its own reach.
    distances = _reach_forward(db, version_ref)
    reached: list[str] = []
    for change_id, root in roots:
        if root not in distances:
            continue
        hop, edge_source = distances[root]
        if hop == 0:
            continue  # the change already recorded its own root
        record_impacts(db, change_id, {version_ref: (hop, edge_source)}, detected_at, sequence)
        reached.append(change_id)
    return reached


def _reach_forward(db: sqlite3.Connection, start: str) -> dict[str, tuple[int, str]]:
    """Versions reachable from ``start`` along support edges, with the distance
    and the label of the first edge taken out of ``start``."""
    seen: dict[str, tuple[int, str]] = {start: (0, "root")}
    frontier = [start]
    hop = 0
    while frontier:
        hop += 1
        following = []
        for user_ref in frontier:
            rows = db.execute(
                "SELECT used_ref,from_dependencies,from_evidence"
                " FROM knowledge_support_edges WHERE user_ref=?",
                (user_ref,),
            ).fetchall()
            for used_ref, from_dependencies, from_evidence in rows:
                if used_ref in seen:
                    continue
                label = (
                    _edge_source(from_dependencies, from_evidence)
                    if user_ref == start
                    else seen[user_ref][1]
                )
                seen[used_ref] = (hop, label)
                following.append(used_ref)
        frontier = following
    return seen


# --- read-boundary evaluation --------------------------------------------

DISPOSITION_KINDS = ("unresolved", "retained_with_evidence", "revised", "retracted")
OPEN_DISPOSITIONS = (None, "unresolved")
IN_USE_WORK_BOUND = 10_000


def read_bound(db: sqlite3.Connection) -> int:
    """The last committed event. Read queries default to this.

    Deliberately not the write sequence (R25, 2026-09-21): a read-only query
    that returned ``MAX+1`` would hand back a boundary that the *next* write is
    about to occupy, so replaying at that boundary would show a change made
    after the query. The two must be different functions.
    """
    return int(db.execute("SELECT COALESCE(MAX(event_id),0) FROM events").fetchone()[0])


def next_sequence(db: sqlite3.Connection) -> int:
    """The sequence a write performed now will carry."""
    return read_bound(db) + 1


def valid_impacts(
    db: sqlite3.Connection, bound: int | None = None, *, version: str | None = None
) -> list[sqlite3.Row]:
    """Impact rows in force at a read boundary (R9).

    Both conditions matter. Without ``detected_sequence <= bound`` a replay at
    a publication's own sequence would see impacts detected after it was
    published, and the stored check could never be reproduced.
    """
    limit = read_bound(db) if bound is None else bound
    query = (
        "SELECT i.* FROM knowledge_impacts i"
        " LEFT JOIN knowledge_scope_revisions s"
        "  ON s.scope_revision_id = i.voided_by_scope_revision"
        " WHERE i.detected_sequence <= ?"
        "  AND (i.voided_by_scope_revision IS NULL OR s.source_sequence > ?)"
    )
    params: list = [limit, limit]
    if version is not None:
        query += " AND i.affected_version = ?"
        params.append(version)
    return db.execute(query, params).fetchall()


def current_disposition(
    db: sqlite3.Connection, change_id: str, affected_version: str, bound: int | None = None
) -> sqlite3.Row | None:
    """R22. The latest disposition for this key at the boundary.

    Dispositions are append-only, so the effective one is the newest row, not
    the union of what has ever been recorded: an ``unresolved`` in the history
    must not keep ``needs_action`` true forever.
    """
    limit = read_bound(db) if bound is None else bound
    return db.execute(
        "SELECT * FROM knowledge_dispositions"
        " WHERE change_id=? AND affected_version=? AND source_sequence<=?"
        " ORDER BY source_sequence DESC, rowid DESC LIMIT 1",
        (change_id, affected_version, limit),
    ).fetchone()


def _support_used(db: sqlite3.Connection, version: str) -> list[tuple[str, int, int]]:
    return [
        (row[0], row[1], row[2])
        for row in db.execute(
            "SELECT used_ref,from_dependencies,from_evidence"
            " FROM knowledge_support_edges WHERE user_ref=?",
            (version,),
        )
    ]


def _revision_row(db: sqlite3.Connection, version: str) -> sqlite3.Row | None:
    knowledge_id, revision = parse_knowledge_ref(version)
    return db.execute(
        "SELECT status,(SELECT MAX(revision) FROM knowledge_revisions WHERE knowledge_id=?)"
        " AS latest FROM knowledge_revisions WHERE knowledge_id=? AND revision=?",
        (knowledge_id, knowledge_id, revision),
    ).fetchone()


def needs_action(db: sqlite3.Connection, version: str, bound: int | None = None) -> bool:
    """R20. Any valid impact on this version whose effective disposition is
    absent or ``unresolved``. Several changes are OR-ed."""
    for impact in valid_impacts(db, bound, version=version):
        disposition = current_disposition(db, impact["change_id"], version, bound)
        if (disposition["kind"] if disposition else None) in OPEN_DISPOSITIONS:
            return True
    return False


def residual_use_risk(db: sqlite3.Connection, version: str, bound: int | None = None) -> list[dict]:
    """R20. Every reason that holds, listed rather than collapsed.

    ``RR2`` is evaluated per ``(change_id, affected_version)`` key. That is what
    lets ``retained_with_evidence`` close one settled problem without erasing
    the fact that the version was once in scope, and without silencing a
    different cause (R23).
    """
    reasons: list[dict] = []
    for used_ref, _dependencies, _evidence in _support_used(db, version):
        row = _revision_row(db, used_ref)
        if row is not None and row["status"] == "retracted":
            reasons.append({"reason": "retracted_ref", "source": used_ref})
    for impact in valid_impacts(db, bound, version=version):
        disposition = current_disposition(db, impact["change_id"], version, bound)
        if (disposition["kind"] if disposition else None) in OPEN_DISPOSITIONS:
            reasons.append({"reason": "in_change_scope", "change_id": impact["change_id"]})
    for used_ref, _dependencies, _evidence in _support_used(db, version):
        for impact in valid_impacts(db, bound, version=used_ref):
            disposition = current_disposition(db, impact["change_id"], used_ref, bound)
            if disposition is not None and disposition["kind"] in ("revised", "retracted"):
                reasons.append(
                    {
                        "reason": "disposed_old_ref",
                        "source": used_ref,
                        "change_id": impact["change_id"],
                        "disposition": disposition["kind"],
                    }
                )
    return reasons


def version_notices(
    db: sqlite3.Connection, version: str, bound: int | None = None
) -> list[dict]:
    """R19. Shown beside the risks and never counted as one.

    A newer revision existing is a pointer, not a verdict; treating it as
    invalidation would turn every rewording into a review demand for every
    downstream user.
    """
    limit = read_bound(db) if bound is None else bound
    notices: list[dict] = []
    for used_ref, _dependencies, _evidence in _support_used(db, version):
        row = _revision_row(db, used_ref)
        if row is None:
            continue
        entry, revision = parse_knowledge_ref(used_ref)
        # Bounded: a revision written after the boundary must not appear when
        # replaying an older check (R25), or a stored snapshot can never be
        # reproduced once the entry moves on.
        latest = db.execute(
            "SELECT MAX(revision) FROM knowledge_revisions"
            " WHERE knowledge_id=? AND COALESCE(source_sequence,0)<=?",
            (entry, limit),
        ).fetchone()[0]
        if latest is not None and revision < latest:
            notices.append({"notice": "newer_revision_exists", "source": used_ref})
        if row["status"] == "superseded":
            notices.append({"notice": "superseded_ref", "source": used_ref})
    return notices


def anchor_set(db: sqlite3.Connection) -> set[str]:
    """R18. Computed once per query and reused across the traversal.

    Scanning ``nodes.inputs`` as JSON is acceptable here where scanning the
    revisions would not be: nodes are one per exploration, while revisions grow
    with every knowledge write. If node counts ever make this measurable, the
    pins should be materialised; until then no structure is added for it.
    """
    anchors: set[str] = set()
    for knowledge_id, revision, status in db.execute(
        "SELECT r.knowledge_id, r.revision, r.status FROM knowledge_revisions r"
        " JOIN (SELECT knowledge_id, MAX(revision) AS top FROM knowledge_revisions"
        "       GROUP BY knowledge_id) m"
        "  ON m.knowledge_id = r.knowledge_id AND m.top = r.revision"
    ):
        # A retracted latest revision is not an anchor (2026-09-21 adjudication).
        # Retraction appends a revision that inherits the old basis edges, so
        # treating it as live would pin its whole upstream as in use forever.
        # A pinned retracted version still becomes an anchor below.
        if status != "retracted":
            anchors.add(f"knowledge/{knowledge_id}@{revision}")
    for knowledge_id, revision in db.execute(
        "SELECT DISTINCT knowledge_id, revision FROM publication_knowledge"
    ):
        anchors.add(f"knowledge/{knowledge_id}@{revision}")
    for (inputs,) in db.execute("SELECT inputs FROM nodes"):
        try:
            refs = json.loads(inputs or "[]")
        except json.JSONDecodeError:
            continue
        for ref in refs if isinstance(refs, list) else []:
            if is_knowledge_ref(ref):
                anchors.add(ref)
    return anchors


def in_use(
    db: sqlite3.Connection,
    version: str,
    *,
    anchors: set[str] | None = None,
    work_bound: int = IN_USE_WORK_BOUND,
) -> dict:
    """R18. Is any anchor able to reach this version along support edges?

    Stated as reachability rather than recursion, which is what makes the
    termination argument simple: an edge may only point at a version that
    already existed, so creation order strictly decreases along edges and the
    support graph is acyclic by construction. No depth limit is imposed --
    a depth limit would turn "still in use" into "not in use", the same silent
    downgrade the coverage rules forbid. The work bound below yields
    ``unknown`` instead, which the caller must present as in use.
    """
    anchors = anchor_set(db) if anchors is None else anchors
    if version in anchors:
        return {"in_use": True, "reason": "anchor"}
    seen = {version}
    frontier = [version]
    while frontier:
        following: list[str] = []
        for used_ref in frontier:
            for user_ref, _dependencies, _evidence in _citers(db, used_ref):
                if user_ref in seen:
                    continue
                if user_ref in anchors:
                    return {"in_use": True, "reason": "cited_by_anchor", "via": user_ref}
                seen.add(user_ref)
                if len(seen) > work_bound:
                    return {"in_use": "unknown", "reason": "work_bound_exhausted"}
                following.append(user_ref)
        frontier = following
    return {"in_use": False, "reason": "no_anchor_reaches_it"}


def write_disposition(
    db: sqlite3.Connection,
    *,
    disposition_id: str,
    change_id: str,
    affected_version: str,
    kind: str,
    reason: str,
    evidence_refs: list[str],
    replacement_ref: str | None,
    author: str,
    operation_id: str,
    created_at: str,
    sequence: int,
) -> dict:
    """R21. Append a disposition for one ``(change_id, affected_version)``.

    Append-only on purpose: the audit trail of how a problem was handled is
    itself evidence, and a later ``retained_with_evidence`` must not erase the
    ``unresolved`` that preceded it. Only this exact key is touched -- never
    other targets of the same change, never other causes on the same target.
    """
    if kind not in DISPOSITION_KINDS:
        raise ValidationError(f"disposition kind must be one of {', '.join(DISPOSITION_KINDS)}")
    if not db.execute(
        "SELECT 1 FROM knowledge_impacts WHERE change_id=? AND affected_version=?",
        (change_id, affected_version),
    ).fetchone():
        raise ValidationError(
            f"no impact record for ({change_id}, {affected_version}) to dispose"
        )
    if kind == "revised" and not replacement_ref:
        raise ValidationError("a 'revised' disposition must name the replacement version")
    db.execute(
        "INSERT INTO knowledge_dispositions"
        " (disposition_id,change_id,affected_version,kind,reason,evidence_refs,"
        "  replacement_ref,author,operation_id,created_at,source_sequence)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            disposition_id,
            change_id,
            affected_version,
            kind,
            reason,
            json.dumps(evidence_refs, ensure_ascii=False),
            replacement_ref,
            author,
            operation_id,
            created_at,
            sequence,
        ),
    )
    # A convenience pointer for the interface only. Anything evaluated against
    # a read boundary must derive the effective disposition from the table, or
    # replaying an old boundary would see a disposition made after it.
    db.execute(
        "UPDATE knowledge_impacts SET disposition_ref=?"
        " WHERE change_id=? AND affected_version=?",
        (disposition_id, change_id, affected_version),
    )
    return {
        "disposition_id": disposition_id,
        "change_id": change_id,
        "affected_version": affected_version,
        "kind": kind,
    }


# --- coverage, explanation paths and bounded queries ---------------------


def effective_scope(db: sqlite3.Connection, change_id: str, bound: int | None = None) -> dict:
    """R9. The scope in force at a boundary: the newest scope revision at or
    below it, falling back to the original declaration. The original is never
    overwritten, so an older boundary still reproduces the wider set."""
    limit = read_bound(db) if bound is None else bound
    row = db.execute(
        "SELECT affected_scope_mode,affected_scope FROM knowledge_scope_revisions"
        " WHERE change_id=? AND source_sequence<=?"
        " ORDER BY source_sequence DESC, rowid DESC LIMIT 1",
        (change_id, limit),
    ).fetchone()
    if row is None:
        row = db.execute(
            "SELECT affected_scope_mode,affected_scope FROM knowledge_changes WHERE change_id=?",
            (change_id,),
        ).fetchone()
    if row is None:
        return {"mode": "none", "scope": []}
    return {"mode": row[0], "scope": json.loads(row[1] or "[]")}


def uncovered_refs(db: sqlite3.Connection, version: str) -> list[dict]:
    """R16. References this release does not propagate through.

    Reported as uncovered, never as "no impact": frozen bytes are not a
    guarantee that a conclusion still holds.
    """
    row = db.execute(
        "SELECT dependencies,evidence_refs FROM knowledge_revisions"
        " WHERE knowledge_id=? AND revision=?",
        parse_knowledge_ref(version),
    ).fetchone()
    if row is None:
        return []
    found: list[dict] = []
    for field, raw in (("dependencies", row[0]), ("evidence_refs", row[1])):
        try:
            refs = json.loads(raw or "[]")
        except json.JSONDecodeError:
            continue
        for ref in refs if isinstance(refs, list) else []:
            if not is_knowledge_ref(ref):
                found.append({"ref": ref, "field": field, "status": "reference type not covered"})
    return found


def explain_path(db: sqlite3.Connection, version: str, roots: list[str]) -> list[dict]:
    """One shortest path from an affected version to a change root.

    Only the information needed to rebuild a path is stored; paths themselves
    are not enumerated (R14). One is returned by default and the caller marks
    whether others exist.
    """
    if version in roots:
        return []
    parents: dict[str, tuple[str, str]] = {}
    seen = {version}
    frontier = [version]
    while frontier:
        following: list[str] = []
        for user_ref in frontier:
            for used_ref, from_dependencies, from_evidence in _support_used(db, user_ref):
                if used_ref in seen:
                    continue
                parents[used_ref] = (user_ref, _edge_source(from_dependencies, from_evidence))
                if used_ref in roots:
                    path = []
                    cursor = used_ref
                    while cursor in parents:
                        origin, source = parents[cursor]
                        path.append({"from": origin, "to": cursor, "source": source})
                        cursor = origin
                    path.reverse()
                    return path
                seen.add(used_ref)
                following.append(used_ref)
        frontier = following
    return []


def _path_count(
    db: sqlite3.Connection,
    node: str,
    roots: set,
    cap: int,
    memo: dict,
    visiting: set,
) -> int:
    if node in roots:
        return 1
    if node in memo:
        return memo[node]
    if node in visiting:
        return 0  # the support graph is acyclic by construction; do not rely on it
    visiting.add(node)
    total = 0
    for used_ref, _dependencies, _evidence in _support_used(db, node):
        total += _path_count(db, used_ref, roots, cap, memo, visiting)
        if total >= cap:
            break
    visiting.discard(node)
    memo[node] = min(total, cap)
    return memo[node]


def has_alternate_path(db: sqlite3.Connection, version: str, roots: list[str]) -> bool:
    """Whether the path shown is not the only one.

    Counting the target's own outgoing edges is not enough: in a diamond one
    step below the fork the target has a single out-edge and still reaches the
    root two ways (CE-14d). Paths are counted on the reachable subgraph with
    memoisation and a cap of two, so no full enumeration is needed and the
    rules' "do not compute the total" still holds.
    """
    return _path_count(db, version, set(roots), 2, {}, set()) > 1


def change_roots(db: sqlite3.Connection, change_id: str, bound: int | None = None) -> list[str]:
    row = db.execute(
        "SELECT knowledge_id,new_revision FROM knowledge_changes WHERE change_id=?", (change_id,)
    ).fetchone()
    if row is None:
        return []
    scope = effective_scope(db, change_id, bound)
    return candidate_roots(db, row[0], scope["mode"], scope["scope"], int(row[1]))


def targets_are_complete(db: sqlite3.Connection, rows, bound: int | None) -> bool:
    """R15. Is the affected set determined?

    A property of the whole set, never of a page: it must not move when a
    caller changes ``limit`` or when a view shows only its first few rows
    (CE-12v-2). Undetermined for two separate reasons -- a change whose scope
    is still ``unknown`` at this boundary, or a target whose own references are
    not all resolvable versions.
    """
    for row in rows:
        if effective_scope(db, row["change_id"], bound)["mode"] == "unknown":
            return False
        if uncovered_refs(db, row["affected_version"]):
            return False
    return True


def impact_query(
    db: sqlite3.Connection,
    *,
    version: str | None = None,
    change_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    bound: int | None = None,
) -> dict:
    """Bounded impact listing with the three incompleteness flags kept apart."""
    limit_sequence = read_bound(db) if bound is None else bound
    rows = [
        row
        for row in valid_impacts(db, limit_sequence, version=version)
        if change_id is None or row["change_id"] == change_id
    ]
    rows.sort(key=lambda row: (row["change_id"], row["hop"], row["affected_version"]))

    page = rows[offset : offset + limit]
    paths_truncated = False
    items = []
    # Stored ``hop``/``edge_source`` are first-detection values and are never
    # recomputed on disk (A0_SCHEMA.md §4.3). What a reader needs is the
    # distance under the roots in force now, so it is derived here, once per
    # change rather than once per row.
    reachable: dict[str, dict] = {}
    for row in page:
        if row["change_id"] not in reachable:
            reachable[row["change_id"]] = affected_set(
                db, change_roots(db, row["change_id"], limit_sequence)
            )
    for row in page:
        roots = change_roots(db, row["change_id"], limit_sequence)
        derived = reachable[row["change_id"]].get(row["affected_version"])
        scope = effective_scope(db, row["change_id"], limit_sequence)
        unconfirmed = scope["mode"] == "unknown"
        uncovered = uncovered_refs(db, row["affected_version"])
        path = explain_path(db, row["affected_version"], roots)
        if has_alternate_path(db, row["affected_version"], roots):
            paths_truncated = True
        disposition = current_disposition(
            db, row["change_id"], row["affected_version"], limit_sequence
        )
        items.append(
            {
                "change_id": row["change_id"],
                "affected_version": row["affected_version"],
                "hop": derived[0] if derived else row["hop"],
                "edge_source": derived[1] if derived else row["edge_source"],
                "detected_hop": row["hop"],
                "review_state": row["review_state"],
                "scope_unconfirmed": unconfirmed,
                "uncovered_refs": uncovered,
                "disposition": disposition["kind"] if disposition else None,
                "explanation": path,
            }
        )
    return {
        "items": items,
        "total": len(rows),
        "shown": len(page),
        "sequence_bound": limit_sequence,
        "targets_complete": targets_are_complete(db, rows, limit_sequence),
        "targets_truncated": len(page) < len(rows),
        "paths_truncated": paths_truncated,
    }


def publication_reference_risk(
    db: sqlite3.Connection, version: str, bound: int | None = None
) -> list[dict]:
    """R20 as corrected on 2026-09-21: a publication is a *referring* party.

    Asking only whether the pinned version has a problem of its own answers the
    wrong question. Under S13 a ``revised`` disposition deliberately clears the
    old version's own predicates while leaving everyone who still cites it at
    risk -- the publication is one of those citers, so its risk has to be
    derived from the reference, not from the target's own state.
    """
    reasons: list[dict] = []
    row = _revision_row(db, version)
    if row is not None and row["status"] == "retracted":
        reasons.append({"reason": "retracted_ref", "source": version})
    for impact in valid_impacts(db, bound, version=version):
        disposition = current_disposition(db, impact["change_id"], version, bound)
        if disposition is not None and disposition["kind"] in ("revised", "retracted"):
            reasons.append(
                {
                    "reason": "disposed_old_ref",
                    "source": version,
                    "change_id": impact["change_id"],
                    "disposition": disposition["kind"],
                }
            )
    for item in residual_use_risk(db, version, bound):
        # The pinned version's own reasons carry over, retagged so the report
        # says which declared reference produced them.
        reasons.append({**item, "source": version, "origin": item.get("source")})
    return reasons


def publication_check(
    db: sqlite3.Connection, publication_id: str, bound: int | None = None
) -> dict:
    """R25/R26. Check a publication's declared references in one read view.

    Expansion is structural, over the rows in ``publication_knowledge``; the
    prose is never consulted, and two findings that merely share a publication
    do not become each other's premises.
    """
    limit = read_bound(db) if bound is None else bound
    pinned = [
        f"knowledge/{knowledge_id}@{revision}"
        for knowledge_id, revision in db.execute(
            "SELECT knowledge_id,revision FROM publication_knowledge WHERE publication_id=?"
            " ORDER BY knowledge_id,revision",
            (publication_id,),
        )
    ]
    if not pinned:
        # No declared reference is not a pass. It is an unknown.
        return {
            "publication_id": publication_id,
            "sequence_bound": limit,
            "check_scope": {"declared_refs": [], "coverage": "unknown"},
            "result": {"status": "coverage unknown", "versions": {}},
            "targets_complete": False,
            "targets_truncated": False,
            "paths_truncated": False,
        }
    versions: dict[str, dict] = {}
    targets_complete = True
    targets_truncated = False
    paths_truncated = False
    for version in pinned:
        page = impact_query(db, version=version, bound=limit)
        targets_complete = targets_complete and page["targets_complete"]
        targets_truncated = targets_truncated or page["targets_truncated"]
        paths_truncated = paths_truncated or page["paths_truncated"]
        uncovered = uncovered_refs(db, version)
        if uncovered:
            # A version with no impact rows can still be incompletely checked
            # (R16). Reading completeness off the impact page alone would call
            # such a publication clear while listing an uncovered reference.
            targets_complete = False
        versions[version] = {
            "needs_action": needs_action(db, version, limit),
            "residual_use_risk": publication_reference_risk(db, version, limit),
            "version_notices": [
                {**notice, "source": version} for notice in version_notices(db, version, limit)
            ],
            "uncovered_refs": uncovered,
            "impacts": page["items"],
        }
    flagged = [
        ref
        for ref, value in versions.items()
        if value["needs_action"] or value["residual_use_risk"]
    ]
    if flagged:
        status = "attention"
    elif any(value["uncovered_refs"] for value in versions.values()):
        status = "coverage partial"
    else:
        status = "no impact found"
    return {
        "publication_id": publication_id,
        "sequence_bound": limit,
        "check_scope": {"declared_refs": pinned, "coverage": "declared references only"},
        "result": {"status": status, "flagged": flagged, "versions": versions},
        "targets_complete": targets_complete,
        "targets_truncated": targets_truncated,
        "paths_truncated": paths_truncated,
    }


def _snapshot_result(result: dict) -> dict:
    """Strip review progress from what gets frozen.

    ``review_state`` is where a review has got to, not what the check found
    (R25). Freezing it would make a stored snapshot irreproducible the moment
    someone picks the item up.
    """
    versions = {}
    for version, value in result.get("versions", {}).items():
        versions[version] = {
            **value,
            "impacts": [
                {key: field for key, field in item.items() if key != "review_state"}
                for item in value.get("impacts", [])
            ],
        }
    return {**result, "versions": versions}


def store_publication_check(
    db: sqlite3.Connection, check_id: str, check: dict, created_at: str
) -> None:
    """Saved in the publishing transaction and never updated afterwards."""
    db.execute(
        "INSERT INTO publication_checks"
        " (check_id,publication_id,sequence_bound,check_scope,result,"
        "  targets_complete,targets_truncated,paths_truncated,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            check_id,
            check["publication_id"],
            check["sequence_bound"],
            json.dumps(check["check_scope"], ensure_ascii=False),
            json.dumps(_snapshot_result(check["result"]), ensure_ascii=False),
            int(check["targets_complete"]),
            int(check["targets_truncated"]),
            int(check["paths_truncated"]),
            created_at,
        ),
    )


def narrow_scope(
    db: sqlite3.Connection,
    *,
    scope_revision_id: str,
    change_id: str,
    scope: list[str],
    reason: str,
    operation_id: str,
    created_at: str,
    sequence: int,
) -> dict:
    """R9. Append an auditable narrowing and void the rows it drops.

    The subset test is against the currently effective scope, not the original
    candidate roots. Testing against the original would let two successive
    "narrowings" end up wider than the first, and would require un-voiding rows
    that this storage cannot express.
    """
    change = db.execute(
        "SELECT knowledge_id,new_revision FROM knowledge_changes WHERE change_id=?", (change_id,)
    ).fetchone()
    if change is None:
        raise ValidationError(f"Unknown change: {change_id}")
    declared = db.execute(
        "SELECT affected_scope_mode FROM knowledge_changes WHERE change_id=?", (change_id,)
    ).fetchone()[0]
    if declared != "unknown":
        raise ValidationError("only a change declared with an unknown scope can be narrowed")
    # The subset basis is what is in force now, not the original candidate
    # roots: testing against the originals would accept [A@1,A@2] -> [A@2] ->
    # [A@1], whose second step is a widening (R9 supplement).
    effective = effective_scope(db, change_id)
    current = set(
        candidate_roots(
            db,
            change["knowledge_id"],
            effective["mode"],
            effective["scope"],
            int(change["new_revision"]),
        )
    )
    if not scope:
        raise ValidationError("a narrowed scope must name at least one version")
    for item in scope:
        if item not in current:
            raise ValidationError(f"{item} is not inside the scope currently in force")
    db.execute(
        "INSERT INTO knowledge_scope_revisions"
        " (scope_revision_id,change_id,affected_scope_mode,affected_scope,reason,"
        "  operation_id,created_at,source_sequence)"
        " VALUES (?,?,'versions',?,?,?,?,?)",
        (
            scope_revision_id,
            change_id,
            json.dumps(scope, ensure_ascii=False),
            reason,
            operation_id,
            created_at,
            sequence,
        ),
    )
    surviving = affected_set(db, scope)
    dropped = [
        row["affected_version"]
        for row in db.execute(
            "SELECT affected_version FROM knowledge_impacts"
            " WHERE change_id=? AND voided_by_scope_revision IS NULL",
            (change_id,),
        )
        if row["affected_version"] not in surviving
    ]
    for version in dropped:
        # Kept, not deleted: replaying an older boundary must still see it.
        db.execute(
            "UPDATE knowledge_impacts SET voided_by_scope_revision=?"
            " WHERE change_id=? AND affected_version=?",
            (scope_revision_id, change_id, version),
        )
    return {
        "scope_revision_id": scope_revision_id,
        "change_id": change_id,
        "affected_scope": scope,
        "voided": dropped,
    }


# --- the narrow knowledge-relation protocol (S2 / R5) --------------------

RESERVED_LABELS = ("grounded_in", "answers", "supersedes", "complements", "challenges")
NARROW_PROTOCOL_HINT = (
    "knowledge-to-knowledge relations with a reserved label must be written through"
    " research_memory's relations[] parameter, which stores them with the revision"
)


def normalise_relations(relations: object, version_ref: str | None = None) -> list[dict]:
    """Shape check for ``relations[]``. Targets must be exact versions."""
    if relations in (None, []):
        return []
    if not isinstance(relations, list):
        raise ValidationError("relations must be a list of {type, target} objects")
    normalised: list[dict] = []
    for item in relations:
        if not isinstance(item, dict):
            raise ValidationError("relations entries must be objects")
        kind = item.get("type")
        target = item.get("target")
        if kind not in RESERVED_LABELS:
            raise ValidationError(f"relation type must be one of {', '.join(RESERVED_LABELS)}")
        if not is_knowledge_ref(target):
            raise ValidationError(f"relation target must be an exact version: {target!r}")
        if kind == "supersedes" and version_ref is not None and target == version_ref:
            raise ValidationError("a version cannot supersede itself")
        normalised.append({"type": kind, "target": target})
    return normalised


def grounded_targets(relations: list[dict]) -> list[str]:
    """``grounded_in`` is not stored as a relation. It is the declared basis,
    so it merges into ``dependencies`` and nowhere else; keeping a second copy
    would create a parallel truth that nothing consumes."""
    return [item["target"] for item in relations if item["type"] == "grounded_in"]


def write_declared_relations(
    db: sqlite3.Connection,
    *,
    version_ref: str,
    relations: list[dict],
    operation_id: str,
    created_at: str,
    next_id,
    asserted_at: dict | None = None,
) -> list[dict]:
    """Store the declaration-only relations beside the revision.

    None of them changes the target's status. ``answers`` does not resolve a
    question and ``challenges`` coexists with what it challenges; the ledger
    records that a claim was made, not that it won.
    """
    written = []
    for item in relations:
        if item["type"] == "grounded_in":
            continue
        knowledge_id, revision = parse_knowledge_ref(item["target"])
        row = db.execute(
            "SELECT e.kind FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id)"
            " WHERE e.knowledge_id=? AND r.revision=?",
            (knowledge_id, revision),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Unknown research reference: {item['target']}")
        if item["type"] == "answers" and row[0] != "open_question":
            raise ValidationError("'answers' may only point at an open_question")
        # Must be the same counter name the generic relate() uses: both mint
        # R-nnn into one table, so two counters collide on the primary key.
        relation_id = next_id(db, "relation", "R")
        db.execute(
            "INSERT INTO relations"
            " (relation_id,source_ref,target_ref,label,note,created_at,relation_type,"
            "  scheduling,operation_id,asserted_at)"
            " VALUES (?,?,?,?,'',?,'knowledge',0,?,?)",
            (relation_id, version_ref, item["target"], item["type"], created_at, operation_id, json.dumps(asserted_at, ensure_ascii=False, sort_keys=True, allow_nan=False)),
        )
        written.append({"relation_id": relation_id, **item})
    return written


def reject_reserved_relate(source_ref: str, target_ref: str, label: str) -> None:
    """R5. The generic relate tool keeps its node and publication duties, but a
    reserved knowledge-to-knowledge label there would be a bypass that nothing
    consumes: it writes a relation row without touching the basis."""
    if label in RESERVED_LABELS and is_knowledge_ref(source_ref) and is_knowledge_ref(target_ref):
        raise ValidationError(f"{label!r} is a reserved knowledge relation. {NARROW_PROTOCOL_HINT}")
