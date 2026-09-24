"""0.6.7 regressions.

Every expectation here is taken from ``docs/067/A0_COUNTEREXAMPLES.md`` and
must not be rewritten from implementation output. Where a test encodes a rule
rather than a numbered counterexample the rule id from ``docs/067/A0_RULES.md``
is named in the test docstring.
"""

import json
import sqlite3

import pytest

from auto_research.errors import ConflictError, ValidationError
from auto_research.memory_store import support_refs
from auto_research.native_store import SCHEMA_VERSION, NativeStore
from auto_research.schema7 import rebuild_support_edges


SCHEMA7_TABLES = (
    "knowledge_support_edges",
    "knowledge_changes",
    "knowledge_scope_revisions",
    "knowledge_impacts",
    "knowledge_dispositions",
    "publication_checks",
)


def store(tmp_path):
    instance = NativeStore(tmp_path / "project")
    instance.initialize("0.6.7 fixture", "initialize")
    instance.associate("host", "main", "associate")
    instance.workflow(
        "host", "main", "register", {"role": "main", "cwd": str(tmp_path)}, "role"
    )
    return instance


def record(instance, name, *, dependencies=None, evidence=None, motivated_by=None, kind="decision"):
    value = instance.record_knowledge(
        {
            "kind": kind,
            "statement": f"statement {name}",
            "dependencies": dependencies or [],
            "evidence_refs": evidence or [],
            "motivated_by": motivated_by or [],
        },
        f"record-{name}",
    )
    return value["ref"]


def edges(instance):
    with instance._read() as db:
        return {
            (row["user_ref"], row["used_ref"]): (
                row["from_dependencies"],
                row["from_evidence"],
            )
            for row in db.execute("SELECT * FROM knowledge_support_edges")
        }


def downgrade_to_schema6(instance):
    """Turn a schema-7 database back into a schema-6 one, so the migration and
    its backfill can be exercised on data that predates the new tables."""
    with sqlite3.connect(instance.db_path, isolation_level=None) as db:
        for table in SCHEMA7_TABLES:
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute("ALTER TABLE knowledge_revisions DROP COLUMN motivated_by")
        db.execute("ALTER TABLE relations DROP COLUMN operation_id")
        for table, column in [("knowledge_revisions", "asserted_at"), ("knowledge_revisions", "execution_refs"), ("relations", "asserted_at"), ("publications", "asserted_at")]:
            if column in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        db.execute("DROP TABLE IF EXISTS field_checks")
        if "checks" in {r[1] for r in db.execute("PRAGMA table_info(knowledge_revisions)")}:
            db.execute("ALTER TABLE knowledge_revisions DROP COLUMN checks")
        db.execute("PRAGMA user_version=6")


# --- R2: the unified knowledge propagation basis -------------------------


def test_support_refs_merges_both_fields_and_keeps_source_labels():
    """R2. An edge carried by both fields must keep both labels; clearing only
    ``dependencies`` leaves the edge in place through ``evidence_refs``."""
    both = support_refs(["knowledge/K-001@1"], ["knowledge/K-001@1"])
    assert both == {"knowledge/K-001@1": {"from_dependencies": 1, "from_evidence": 1}}

    # CE-13v: B@2 clears dependencies but inherits evidence_refs=[A@1].
    inherited = support_refs([], ["knowledge/K-001@1"])
    assert inherited == {"knowledge/K-001@1": {"from_dependencies": 0, "from_evidence": 1}}

    # CE-13 main: nothing left once neither field cites A.
    assert support_refs([], ["S-002"]) == {}


def test_support_refs_treats_frozen_material_as_terminal():
    """R2. Snapshots and publication items are terminal and never become edges."""
    assert support_refs(["pub/P-001"], ["S-001", "S-002#inner/file.txt"]) == {}
    assert support_refs(["knowledge/K-009"], []) == {}, "entry refs without a version"


def test_support_refs_accepts_json_text_and_rejects_malformed():
    assert support_refs('["knowledge/K-001@2"]', "[]") == {
        "knowledge/K-001@2": {"from_dependencies": 1, "from_evidence": 0}
    }
    assert support_refs("not json", None) == {}


# --- A1-1: schema 7, migration and backfill ------------------------------


def test_fresh_project_is_schema7_with_every_epistemic_table(tmp_path):
    instance = store(tmp_path)
    with instance._read() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 9
        present = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert set(SCHEMA7_TABLES) <= present
        columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_revisions)")}
        assert "motivated_by" in columns


def test_recording_knowledge_materialises_support_edges(tmp_path):
    instance = store(tmp_path)
    a = record(instance, "a")
    record(instance, "b", dependencies=[a], evidence=[a])
    record(instance, "c")
    assert edges(instance) == {
        ("knowledge/K-002@1", a): (1, 1),
    }, "an edge carried by both fields keeps both labels; unrelated records add none"


def test_schema6_migration_backs_up_backfills_and_is_idempotent(tmp_path):
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    record(instance, "c", evidence=[b])
    expected = edges(instance)

    downgrade_to_schema6(instance)
    upgraded = NativeStore(instance.root)

    backup = instance.meta / "schema-6-backup.sqlite3"
    assert backup.exists()
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    with upgraded._read() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert edges(upgraded) == expected, "backfill reconstructs edges from the revisions"

    # Reopening an already-migrated project must not repeat the work.
    again = NativeStore(instance.root)
    assert edges(again) == expected


def test_backfill_reports_zero_edges_on_a_snapshot_only_ledger(tmp_path):
    """The real 3080 ledger cites whole snapshots only, so its backfill yields
    nothing. Migration must never invent an edge to hide that.

    The revisions are written directly because that is the shape migration
    actually meets: rows already on disk, written before the edge table existed.
    """
    instance = store(tmp_path)
    record(instance, "a")
    with sqlite3.connect(instance.db_path, isolation_level=None) as db:
        db.execute(
            "UPDATE knowledge_revisions SET evidence_refs=?, dependencies=?",
            ('["S-001", "S-002"]', '["pub/P-001"]'),
        )
        assert rebuild_support_edges(db) == 0
    assert edges(instance) == {}


def test_rebuild_is_deterministic_and_removes_stale_edges(tmp_path):
    instance = store(tmp_path)
    a = record(instance, "a")
    record(instance, "b", dependencies=[a])
    expected = edges(instance)
    with sqlite3.connect(instance.db_path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "INSERT INTO knowledge_support_edges"
            " (user_ref,used_ref,from_dependencies,from_evidence,created_at,source_sequence)"
            " VALUES ('knowledge/K-999@1','knowledge/K-998@1',1,0,'now',0)"
        )
        assert rebuild_support_edges(db) == len(expected)
    assert edges(instance) == expected


def test_support_edge_rejects_an_edge_with_no_source_label(tmp_path):
    """The CHECK constraint keeps a labelless edge out of the table."""
    instance = store(tmp_path)
    with sqlite3.connect(instance.db_path, isolation_level=None) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO knowledge_support_edges"
                " (user_ref,used_ref,from_dependencies,from_evidence,created_at,source_sequence)"
                " VALUES ('knowledge/K-001@1','knowledge/K-002@1',0,0,'now',0)"
            )


def test_a_newer_schema_is_refused_rather_than_written(tmp_path):
    """R: an older package must refuse a database it does not understand."""
    instance = store(tmp_path)
    with sqlite3.connect(instance.db_path, isolation_level=None) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(ValidationError, match=f"Schema {SCHEMA_VERSION + 1}"):
        NativeStore(instance.root)


# --- A1-2: change events, affected sets, idempotency ---------------------


def revise(
    instance,
    ref,
    changes,
    *,
    mode,
    scope=None,
    kind=None,
    reason="because",
    op=None,
):
    fields = {
        "ref": ref,
        "expected_revision": int(ref.rsplit("@", 1)[1]),
        "changes": changes,
        "reason": reason,
        "affected_scope_mode": mode,
    }
    if scope is not None:
        fields["affected_scope"] = scope
    if kind is not None:
        fields["change_kind"] = kind
    return instance.revise_knowledge(fields, op or f"revise-{ref}-{reason}")


def retract(instance, ref, *, op=None):
    """The fixed shape of a retraction: a new revision carrying the retracted
    status plus a change event naming the version it invalidates."""
    return revise(
        instance,
        ref,
        {"status": "retracted"},
        mode="versions",
        scope=[ref],
        kind="retract",
        reason="retract",
        op=op,
    )


def impacts(instance):
    with instance._read() as db:
        return sorted(
            (row["change_id"], row["affected_version"], row["hop"], row["edge_source"])
            for row in db.execute("SELECT * FROM knowledge_impacts")
        )


def change_count(instance):
    with instance._read() as db:
        return db.execute("SELECT COUNT(*) FROM knowledge_changes").fetchone()[0]


def test_ce1_replay_is_idempotent_and_a_second_change_still_registers(tmp_path):
    """CE-1. The idempotency key carries the change id, so a replay adds
    nothing while an independent second change adds its own rows."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])

    retract(instance, a, op="ce1-retract")
    first = impacts(instance)
    assert [(row[1], row[2], row[3]) for row in first] == [
        (a, 0, "root"),
        (b, 1, "dependencies"),
    ]

    # Step 2: replaying the same request must not touch the ledger.
    retract(instance, a, op="ce1-retract")
    assert impacts(instance) == first
    assert change_count(instance) == 1

    # Step 3: a second, independent change on the same version.
    revise(instance, "knowledge/K-001@2", {}, mode="versions", scope=[a], reason="second")
    after = impacts(instance)
    assert len(after) == 4, "a second change must not be swallowed by the first"
    assert {row[0] for row in after} == {"CH-001", "CH-002"}
    assert sorted(row[1] for row in after) == sorted([a, a, b, b])
    assert change_count(instance) == 2


def test_ce4_a_use_pinned_to_an_old_version_is_not_hidden_by_the_latest(tmp_path):
    """CE-4. Traversal walks versions, not entries: C is still found through
    B@1 after B@2 has become independent."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[b])

    revise(instance, b, {"dependencies": []}, mode="none", reason="independent")
    assert change_count(instance) == 1
    assert impacts(instance) == [], "scope=none produces no impact records"

    retract(instance, a)
    rows = [(row[1], row[2]) for row in impacts(instance)]
    assert rows == [(a, 0), (b, 1), (c, 2)]
    assert "knowledge/K-002@2" not in {row[1] for row in impacts(instance)}


def test_ce5_an_editorial_revision_does_not_reach_every_user(tmp_path):
    """CE-5. kind=reword with scope=none records the change and nothing else."""
    instance = store(tmp_path)
    a = record(instance, "a")
    record(instance, "b", dependencies=[a])
    record(instance, "d", dependencies=[a])

    revise(instance, a, {"statement": "reworded"}, mode="none", kind="reword", reason="wording")
    assert change_count(instance) == 1
    assert impacts(instance) == []


def test_ce5_a_retraction_may_not_declare_an_empty_scope(tmp_path):
    """CE-5, contrast case. The one structural contradiction R7 rejects."""
    instance = store(tmp_path)
    a = record(instance, "a")
    with pytest.raises(ValidationError, match="retraction"):
        revise(instance, a, {"status": "retracted"}, mode="none", kind="retract")
    with pytest.raises(ValidationError, match="change_kind"):
        revise(instance, a, {"status": "retracted"}, mode="versions", scope=[a], kind="reword")


def test_ce9_an_indirect_late_reference_is_found_by_reachability(tmp_path):
    """CE-9. C cites B, which is not in the scope at all; only reachability to
    the change root finds it, and the original change id is reused."""
    instance = store(tmp_path)
    a = record(instance, "a")
    retract(instance, a)
    assert [(row[1], row[2]) for row in impacts(instance)] == [(a, 0)]

    b = record(instance, "b", dependencies=[a])
    assert [(row[1], row[2]) for row in impacts(instance)] == [(a, 0), (b, 1)]

    c = record(instance, "c", dependencies=[b])
    rows = impacts(instance)
    assert [(row[1], row[2]) for row in rows] == [(a, 0), (b, 1), (c, 2)]
    assert {row[0] for row in rows} == {"CH-001"}, "a registration is not a new change"
    assert change_count(instance) == 1


def test_ce14_two_paths_give_one_row_at_the_shortest_hop(tmp_path):
    """CE-14. D reaches A two ways; it is recorded once, at hop 2."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[a])
    d = record(instance, "d", dependencies=[b, c])

    retract(instance, a)
    rows = [(row[1], row[2]) for row in impacts(instance)]
    assert rows == [(a, 0), (b, 1), (c, 1), (d, 2)]
    assert [row[1] for row in impacts(instance)].count(d) == 1


def test_ce14_a_generic_relation_cycle_neither_propagates_nor_hangs(tmp_path):
    """CE-14. Free-label relations may cycle; they are not basis edges."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    d = record(instance, "d")
    instance.relate(b, d, "relates_to", "cycle", "rel-1")
    instance.relate(d, b, "relates_to", "cycle back", "rel-2")

    retract(instance, a)
    assert [(row[1], row[2]) for row in impacts(instance)] == [(a, 0), (b, 1)]
    assert d not in {row[1] for row in impacts(instance)}


def test_scope_declarations_are_checked_against_the_entry_and_its_history(tmp_path):
    """R7. The scope may only name already-existing versions of this entry."""
    instance = store(tmp_path)
    a = record(instance, "a")
    other = record(instance, "other")
    with pytest.raises(ValidationError, match="required"):
        instance.revise_knowledge(
            {"ref": a, "expected_revision": 1, "changes": {}, "reason": "no mode"}, "missing-mode"
        )
    with pytest.raises(ValidationError, match="only name versions of"):
        revise(instance, a, {}, mode="versions", scope=[other], reason="wrong entry")
    with pytest.raises(ValidationError, match="already exist"):
        revise(instance, a, {}, mode="versions", scope=["knowledge/K-001@9"], reason="future")
    with pytest.raises(ValidationError, match="must be empty"):
        revise(instance, a, {}, mode="none", scope=[a], reason="contradiction")


def test_unknown_scope_covers_every_earlier_version_and_no_others(tmp_path):
    """R8. Candidate roots are exactly this entry's pre-existing versions."""
    instance = store(tmp_path)
    a = record(instance, "a")
    record(instance, "unrelated")
    revise(instance, a, {"statement": "second"}, mode="none", reason="grow")

    revise(instance, "knowledge/K-001@2", {}, mode="unknown", reason="uncertain")
    affected = {row[1] for row in impacts(instance)}
    assert affected == {"knowledge/K-001@1", "knowledge/K-001@2"}
    assert "knowledge/K-002@1" not in affected


def test_impact_rows_merge_on_the_change_and_version_key_keeping_the_shortest_hop(tmp_path):
    """R13. The stored mechanism, exercised directly rather than through the
    request-level replay: a second write under the same key must merge, take
    the smaller hop, and leave review progress untouched."""
    from auto_research.epistemic import record_impacts

    instance = store(tmp_path)
    a = record(instance, "a")
    retract(instance, a)
    with sqlite3.connect(instance.db_path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        record_impacts(db, "CH-001", {"knowledge/K-009@1": (2, "dependencies")}, "t0", 1)
        db.execute(
            "UPDATE knowledge_impacts SET review_state='running'"
            " WHERE affected_version='knowledge/K-009@1'"
        )
        record_impacts(db, "CH-001", {"knowledge/K-009@1": (1, "evidence_refs")}, "t1", 2)
        rows = db.execute(
            "SELECT * FROM knowledge_impacts WHERE affected_version='knowledge/K-009@1'"
        ).fetchall()
        assert len(rows) == 1, "one row per (change_id, affected_version)"
        assert rows[0]["hop"] == 1, "a shorter path replaces the stale hop"
        assert rows[0]["edge_source"] == "evidence_refs", (
            "hop and edge_source must describe the same path, never disagree"
        )
        assert rows[0]["review_state"] == "running", "review progress survives the merge"
        assert rows[0]["detected_sequence"] == 1, "first detection is when it was found"

        # A different change on the same version is a separate problem.
        db.execute(
            "INSERT INTO knowledge_changes"
            " (change_id,knowledge_id,new_revision,kind,affected_scope_mode,affected_scope,"
            "  reason,operation_id,created_at,source_sequence)"
            " VALUES ('CH-777','K-001',3,'correct','none','[]','r','op','t2',3)"
        )
        record_impacts(db, "CH-777", {"knowledge/K-009@1": (1, "dependencies")}, "t2", 3)
        assert (
            db.execute(
                "SELECT COUNT(*) FROM knowledge_impacts"
                " WHERE affected_version='knowledge/K-009@1'"
            ).fetchone()[0]
            == 2
        )


def test_ce6_late_dependencies_register_heuristics_do_not_and_causes_stay_apart(tmp_path):
    """CE-6. Three things at once: a dependency written after the retraction is
    still found, a motivation is not, and two causes on one target keep two
    separate problems."""
    instance = store(tmp_path)
    a = record(instance, "a")
    d = record(instance, "d")

    retract(instance, a, op="ce6-a")
    e = record(instance, "e", dependencies=[a])
    f = record(instance, "f", motivated_by=[a])
    h = record(instance, "h", dependencies=[a, d])
    retract(instance, d, op="ce6-d")

    rows = impacts(instance)
    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("CH-001", a, 0),
        ("CH-001", e, 1),
        ("CH-001", h, 1),
        ("CH-002", d, 0),
        ("CH-002", h, 1),
    ]
    assert f not in {row[1] for row in rows}, "a motivation never propagates"
    assert len([row for row in rows if row[1] == h]) == 2, "two causes, two problems"
    assert change_count(instance) == 2, "registering a reference is not a change"


def test_motivated_by_is_a_version_snapshot_that_never_becomes_an_edge(tmp_path):
    """R4. It is stored, inherited on revision, and absent from the edge table."""
    instance = store(tmp_path)
    a = record(instance, "a")
    f = record(instance, "f", motivated_by=[a])
    assert edges(instance) == {}

    revised = revise(instance, f, {"statement": "later"}, mode="none", reason="inherit")
    assert revised["motivated_by"] == [a], "an omitted field inherits the previous snapshot"
    assert edges(instance) == {}


# --- A1-4: dispositions and the derived predicates ------------------------


def risk(instance, version, bound=None):
    return instance.knowledge_risk([version], bound)["versions"][version]


def reasons(instance, version, bound=None):
    return sorted(item["reason"] for item in risk(instance, version, bound)["residual_use_risk"])


def dispose(instance, change_id, version, kind, *, replacement=None, op=None):
    return instance.dispose_impact(
        {
            "change_id": change_id,
            "affected_version": version,
            "disposition_kind": kind,
            "reason": f"{kind} by test",
            "replacement_ref": replacement,
        },
        op or f"dispose-{change_id}-{version}-{kind}",
    )


def test_ce2_review_progress_never_clears_the_risk(tmp_path):
    """CE-2. pending, running and a returned proposal must read identically."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    retract(instance, a)

    observed = []
    for state in ("pending", "running", "proposal_ready"):
        if state != "pending":
            instance.impact_review_state("CH-001", b, state, f"progress-{state}")
        with instance._read() as db:
            row = db.execute(
                "SELECT * FROM knowledge_impacts WHERE affected_version=?", (b,)
            ).fetchone()
        observed.append(
            (
                row["review_state"],
                row["disposition_ref"],
                risk(instance, b)["needs_action"],
                reasons(instance, b),
            )
        )
    assert observed == [
        ("pending", None, True, ["in_change_scope"]),
        ("running", None, True, ["in_change_scope"]),
        ("proposal_ready", None, True, ["in_change_scope"]),
    ], "a returned proposal only means the materials are ready"


def test_ce3_evidenced_retention_closes_that_problem_and_a_new_cause_reopens(tmp_path):
    """CE-3. The settled problem stops showing as a current risk, and the
    history of it stays queryable."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])

    retract(instance, a)
    assert risk(instance, b)["needs_action"] is True
    assert reasons(instance, b) == ["in_change_scope"]

    dispose(instance, "CH-001", b, "retained_with_evidence")
    assert risk(instance, b)["needs_action"] is False
    assert reasons(instance, b) == [], "a settled cause is not a standing risk"

    with instance._read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_impacts WHERE affected_version=?", (b,)
        ).fetchone()[0] == 1, "the historical fact of being affected is still there"

    revise(instance, "knowledge/K-001@2", {}, mode="versions", scope=[a], reason="new cause")
    assert risk(instance, b)["needs_action"] is True
    assert reasons(instance, b) == ["in_change_scope"]


def test_ce10b_citing_a_retracted_version_is_a_risk_without_any_scope(tmp_path):
    """CE-10b. RR1 does not depend on membership of any change's scope, so a
    check that only looks at needs_action misses it entirely."""
    instance = store(tmp_path)
    a = record(instance, "a")
    retract(instance, a)
    d = record(instance, "d", evidence=["knowledge/K-001@2"])

    assert d not in {row[1] for row in impacts(instance)}
    assert risk(instance, d)["needs_action"] is False
    assert reasons(instance, d) == ["retracted_ref"]
    assert risk(instance, d)["version_notices"] == [], "the retracted revision is the latest"


def test_ce11_a_revised_disposition_leaves_the_old_citers_at_risk(tmp_path):
    """CE-11. The consequence lands on the references *to* B@1, not on B@1."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[b])
    publication = publish(instance, status="complete", refs=[b], op="pub-ce11")

    retract(instance, a)
    revised = revise(instance, b, {"dependencies": []}, mode="none", reason="independent")
    dispose(instance, "CH-001", b, "revised", replacement=revised["ref"])

    assert risk(instance, b)["needs_action"] is False
    assert reasons(instance, b) == []
    assert risk(instance, c)["needs_action"] is True
    assert reasons(instance, c) == ["disposed_old_ref", "in_change_scope"]
    assert risk(instance, revised["ref"])["needs_action"] is False
    assert reasons(instance, revised["ref"]) == [], "the replacement is judged on its own"

    # The publication is a referring party: B@1's own predicates are false, yet
    # P-001 still pins it and must show the risk (R20 as corrected).
    check = instance.publication_check(publication["publication_id"])
    assert check["result"]["status"] == "attention"
    assert check["result"]["flagged"] == [b]
    p_reasons = check["result"]["versions"][b]["residual_use_risk"]
    assert [item["reason"] for item in p_reasons] == ["disposed_old_ref"]
    assert p_reasons[0]["source"] == b and p_reasons[0]["disposition"] == "revised"
    assert check["result"]["versions"][b]["needs_action"] is False


def test_ce11v_a_retracted_disposition_shows_as_rr3_not_rr1(tmp_path):
    """CE-11v. Three different things are called retracted; only the version
    status produces RR1, only the disposition produces RR3."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[b])
    publication = publish(instance, status="complete", refs=[b], op="pub-ce11v")

    retract(instance, a)
    dispose(instance, "CH-001", b, "retracted")

    assert risk(instance, b)["needs_action"] is False
    assert reasons(instance, c) == ["disposed_old_ref", "in_change_scope"]
    check = instance.publication_check(publication["publication_id"])
    assert check["result"]["status"] == "attention" and check["result"]["flagged"] == [b]
    assert [item["reason"] for item in check["result"]["versions"][b]["residual_use_risk"]] == [
        "disposed_old_ref"
    ]
    detail = [
        item
        for item in risk(instance, c)["residual_use_risk"]
        if item["reason"] == "disposed_old_ref"
    ]
    assert detail[0]["disposition"] == "retracted" and detail[0]["source"] == b


def test_ce12_dispositions_do_not_cross_targets_or_causes(tmp_path):
    """CE-12. unresolved keeps the problem open; retention closes exactly one
    (change, version) key and nothing else."""
    instance = store(tmp_path)
    a = record(instance, "a")
    d = record(instance, "d")
    b = record(instance, "b", dependencies=[a, d])
    c = record(instance, "c", dependencies=[b])

    retract(instance, a, op="ce12-a")
    retract(instance, d, op="ce12-d")

    dispose(instance, "CH-001", b, "unresolved")
    assert risk(instance, b)["needs_action"] is True, "unresolved is not a resolution"

    dispose(instance, "CH-001", b, "retained_with_evidence")
    with instance._read() as db:
        rows = db.execute(
            "SELECT kind FROM knowledge_dispositions WHERE affected_version=? ORDER BY rowid",
            (b,),
        ).fetchall()
        assert [row[0] for row in rows] == ["unresolved", "retained_with_evidence"], "append only"

    assert risk(instance, b)["needs_action"] is True, "the other cause is still open"
    assert risk(instance, c)["needs_action"] is True, "another target is not closed with it"

    dispose(instance, "CH-002", b, "retained_with_evidence")
    assert risk(instance, b)["needs_action"] is False, "both causes now settled"
    assert risk(instance, c)["needs_action"] is True


# --- A1-5: coverage flags, in_use, publication checks --------------------


def publish(instance, *, status, refs, op, summary="fixture"):
    node = instance.propose(f"Q {op}", "now", "plan", f"node-{op}")
    try:
        instance.focus("host", "main", node["node_id"], "planner", "manual", f"focus-{op}")
    except ConflictError:
        pass  # a work segment is already open; publish under it
    return instance.publish_metadata(
        "host",
        "main",
        status,
        summary,
        [],
        [{"item_id": "finding", "kind": "finding", "content": {"value": 1}}],
        op,
        knowledge_refs=refs,
    )


def test_ce10v_in_use_needs_all_three_anchor_criteria(tmp_path):
    """CE-10v. B@1 and D@1 differ only in having a live citer; an
    implementation with just the first two criteria misreports B@1."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[b])
    d = record(instance, "d", dependencies=[a])
    e = record(instance, "e", dependencies=[a])
    g = record(instance, "g", dependencies=[a])
    f = record(instance, "f")

    for version, label in ((b, "b"), (d, "d"), (e, "e"), (g, "g")):
        revise(instance, version, {"dependencies": []}, mode="none", reason=f"{label} moves on")
    publish(instance, status="partial", refs=[f, g], op="pub-anchor")
    # E@1 is anchored only by a node's fixed inputs.
    pinning_node = instance.propose("pinned by inputs", "now", "plan", "node-inputs", inputs=[e])

    assert risk(instance, b)["in_use"] is True, "only reachable from the anchor C@1"
    assert risk(instance, b)["reason"] == "cited_by_anchor"
    assert risk(instance, d)["in_use"] is False, "not latest, not pinned, never cited"
    assert risk(instance, c)["in_use"] is True and risk(instance, c)["reason"] == "anchor"
    assert risk(instance, g)["in_use"] is True, "pinned by a publication"
    assert risk(instance, e)["in_use"] is True, "pinned by a node's inputs"
    assert risk(instance, e)["reason"] == "anchor"

    retract(instance, a)
    rows = [(row[1], row[2]) for row in impacts(instance)]
    # Seven, not six: propose(inputs=[E@1]) writes the inputs into the node's
    # question entry, so that entry depends on E@1 and is genuinely affected.
    # Registered in the plan's change log; the expectation table originally
    # omitted the side effect of its own pinning mechanism.
    question = pinning_node["question_ref"]
    assert sorted(rows) == sorted(
        [(a, 0), (b, 1), (d, 1), (e, 1), (g, 1), (c, 2), (question, 2)]
    ), "exactly these rows, not a subset"
    assert f not in {row[0] for row in rows}, "sharing a publication creates no dependency"
    assert risk(instance, a)["in_use"] is True, "reached through the pinned anchors"


def test_in_use_reports_unknown_rather_than_false_when_it_runs_out_of_budget(tmp_path):
    """R18. Exhausting the work bound must never read as 'not in use'."""
    from auto_research import epistemic

    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    record(instance, "c", dependencies=[b])
    # Neither A@1 nor B@1 may be an anchor itself, or the walk never starts.
    revise(instance, b, {"dependencies": []}, mode="none", reason="b moves on")
    retract(instance, a)
    with instance._read() as db:
        assert epistemic.in_use(db, a)["in_use"] is True, "reached through C@1"
        starved = epistemic.in_use(db, a, work_bound=1)
        assert starved["in_use"] == "unknown", "running out of budget is not an answer"
        assert starved["reason"] == "work_bound_exhausted"


def test_ce8a_a_reference_type_outside_the_release_reads_as_uncovered(tmp_path):
    """CE-8a. Never 'no impact' and never 'all clear'."""
    instance = store(tmp_path)
    publication = publish(instance, status="partial", refs=[], op="pub-uncovered")
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a, f"pub/{publication['publication_id']}"])

    retract(instance, a)
    page = instance.knowledge_impacts(version=b)
    assert page["items"][0]["uncovered_refs"] == [
        {
            "ref": f"pub/{publication['publication_id']}",
            "field": "dependencies",
            "status": "reference type not covered",
        }
    ]
    assert page["targets_complete"] is False


def test_ce8b_a_bounded_page_marks_truncation_without_claiming_incompleteness(tmp_path):
    """CE-8b. The set is fully determined; only the page is short."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[a])
    record(instance, "d", dependencies=[b, c])

    retract(instance, a)
    page = instance.knowledge_impacts(change_id="CH-001", limit=2)
    assert (page["total"], page["shown"]) == (4, 2)
    assert page["targets_truncated"] is True
    assert page["targets_complete"] is True, "truncation is not incompleteness"


def test_ce8c_an_unknown_scope_is_possible_impact_not_established_failure(tmp_path):
    """CE-8c. Candidate roots are exactly the entry's earlier versions."""
    instance = store(tmp_path)
    a = record(instance, "a")
    record(instance, "unrelated")
    revise(instance, a, {"statement": "v2"}, mode="none", reason="grow")
    revise(instance, "knowledge/K-001@2", {}, mode="unknown", reason="uncertain")

    page = instance.knowledge_impacts(change_id="CH-002")
    assert {item["affected_version"] for item in page["items"]} == {
        "knowledge/K-001@1",
        "knowledge/K-001@2",
    }
    assert all(item["scope_unconfirmed"] for item in page["items"])
    assert page["targets_complete"] is False


def test_ce8d_a_publication_without_declared_refs_reports_unknown_coverage(tmp_path):
    """CE-8d."""
    instance = store(tmp_path)
    publication = publish(instance, status="complete", refs=[], op="pub-empty")
    stored = instance.stored_publication_check(publication["publication_id"])
    assert stored["result"]["status"] == "coverage unknown"
    assert stored["targets_complete"] == 0
    assert publication["check"]["status"] == "coverage unknown"


def test_ce14_flags_are_reported_separately_and_the_bound_replays(tmp_path):
    """CE-14. One path shown, others flagged, and the same bound reproduces."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[a])
    d = record(instance, "d", dependencies=[b, c])

    retract(instance, a)
    page = instance.knowledge_impacts(version=d)
    assert len(page["items"]) == 1
    explanation = page["items"][0]["explanation"]
    assert [step["from"] for step in explanation] == [d, explanation[0]["to"]]
    assert explanation[-1]["to"] == a, "a single path, ending at the change root"
    assert page["paths_truncated"] is True, "D reaches A two ways; only one is shown"
    assert page["targets_complete"] is True
    replay = instance.knowledge_impacts(version=d, bound=page["sequence_bound"])
    assert replay["items"] == page["items"]


def test_ce7_a_change_after_publication_is_visible_but_does_not_rewrite_it(tmp_path):
    """CE-7. Replaying at the stored bound reproduces the original verdict."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    publication = publish(instance, status="complete", refs=[b], op="pub-ce7")
    stored = instance.stored_publication_check(publication["publication_id"])
    assert stored["result"]["status"] == "no impact found"
    bound = stored["sequence_bound"]

    retract(instance, a)
    assert {row[1] for row in impacts(instance)} == {a, b}

    after = instance.stored_publication_check(publication["publication_id"])
    assert after == stored, "the stored snapshot is not rewritten"
    assert instance.publication_check(publication["publication_id"], bound)["result"][
        "status"
    ] == "no impact found", "replay at the old bound reproduces the old answer"
    assert (
        instance.publication_check(publication["publication_id"])["result"]["status"]
        == "attention"
    ), "and the current view shows the new impact"


def test_ce10a_a_publication_pinning_the_root_is_prompted_with_no_downstream(tmp_path):
    """CE-10a. hop 0 exists on its own account, and the pin makes it live."""
    instance = store(tmp_path)
    a = record(instance, "a")
    publication = publish(instance, status="complete", refs=[a], op="pub-ce10a")
    assert publication["check"]["status"] == "no impact found"

    retract(instance, a)
    rows = impacts(instance)
    assert [(row[1], row[2], row[3]) for row in rows] == [(a, 0, "root")]
    assert risk(instance, a)["in_use"] is True
    assert risk(instance, a)["reason"] == "anchor", "pinned by publication_knowledge"

    current = instance.publication_check(publication["publication_id"])
    assert current["result"]["status"] == "attention"
    assert current["result"]["flagged"] == [a], "prompted even with nothing downstream"


def test_ce8e_narrowing_an_unknown_scope_is_appended_and_replays_at_the_old_bound(tmp_path):
    """CE-8e. Narrowing never rewrites the declaration and never deletes the
    rows it drops, so an earlier check still reproduces."""
    instance = store(tmp_path)
    a = record(instance, "a")
    revise(instance, a, {"statement": "second"}, mode="none", reason="grow")
    b = record(instance, "b", dependencies=["knowledge/K-001@1"])
    d = record(instance, "d", dependencies=["knowledge/K-001@2"])

    revise(instance, "knowledge/K-001@2", {}, mode="unknown", reason="uncertain")
    assert {row[1] for row in impacts(instance)} == {
        "knowledge/K-001@1",
        "knowledge/K-001@2",
        b,
        d,
    }

    publication = publish(instance, status="complete", refs=[b, d], op="pub-ce8e")
    stored = instance.stored_publication_check(publication["publication_id"])
    assert stored["targets_complete"] == 0, "an unknown scope is not a determined set"
    old_bound = stored["sequence_bound"]

    with pytest.raises(ValidationError, match="not inside the scope"):
        instance.narrow_impact_scope(
            {"change_id": "CH-002", "affected_scope": [b], "reason": "wrong entry"}, "bad-narrow"
        )
    instance.narrow_impact_scope(
        {
            "change_id": "CH-002",
            "affected_scope": ["knowledge/K-001@2"],
            "reason": "only the second version was wrong",
        },
        "narrow",
    )

    with instance._read() as db:
        original = db.execute(
            "SELECT affected_scope_mode FROM knowledge_changes WHERE change_id='CH-002'"
        ).fetchone()[0]
        assert original == "unknown", "the original declaration is preserved"
        assert db.execute("SELECT COUNT(*) FROM knowledge_scope_revisions").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_impacts WHERE change_id='CH-002'"
        ).fetchone()[0] == 4, "dropped rows are voided, not deleted"

    now = instance.knowledge_impacts(change_id="CH-002")
    assert {item["affected_version"] for item in now["items"]} == {"knowledge/K-001@2", d}
    assert risk(instance, b)["needs_action"] is False, "voided rows stop counting"
    assert risk(instance, d)["needs_action"] is True

    replayed = instance.knowledge_impacts(change_id="CH-002", bound=old_bound)
    assert {item["affected_version"] for item in replayed["items"]} == {
        "knowledge/K-001@1",
        "knowledge/K-001@2",
        b,
        d,
    }, "the wider set is still what the earlier boundary saw"


def test_only_an_unknown_scope_can_be_narrowed(tmp_path):
    """R9. A declared 'versions' scope is a statement, not a draft."""
    instance = store(tmp_path)
    a = record(instance, "a")
    retract(instance, a)
    with pytest.raises(ValidationError, match="unknown scope"):
        instance.narrow_impact_scope(
            {"change_id": "CH-001", "affected_scope": [a], "reason": "no"}, "narrow-versions"
        )


# --- A1-6: the narrow relation protocol ----------------------------------


def relations(instance):
    with instance._read() as db:
        return [
            (row["source_ref"], row["target_ref"], row["label"], row["relation_type"])
            for row in db.execute("SELECT * FROM relations ORDER BY relation_id")
        ]


def test_ce13_clearing_dependencies_is_not_reattached_from_history(tmp_path):
    """CE-13. No union over an entry's past versions may put A back."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    second = revise(instance, b, {"dependencies": []}, mode="none", reason="detach")["ref"]

    retract(instance, a)
    assert [(row[1], row[2]) for row in impacts(instance)] == [(a, 0), (b, 1)]
    assert second not in {row[1] for row in impacts(instance)}


def test_ce13_generic_relate_refuses_a_reserved_knowledge_label(tmp_path):
    """CE-13. The bypass would write a row that nothing consumes."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b")
    before = relations(instance)
    with pytest.raises(ValidationError, match="reserved knowledge relation"):
        instance.relate(b, a, "grounded_in", "bypass", "bypass-1")
    assert relations(instance) == before, "nothing was written"
    with instance._read() as db:
        row = db.execute(
            "SELECT dependencies FROM knowledge_revisions WHERE knowledge_id=? AND revision=1",
            (b.split("/")[1].split("@")[0],),
        ).fetchone()
        assert row[0] == "[]", "the existing revision is untouched"

    # Node and publication relations, and free labels, are unaffected.
    instance.relate(b, a, "relates_to", "free label", "free-1")
    assert len(relations(instance)) == len(before) + 1


def test_ce13v_evidence_refs_alone_still_carries_the_basis(tmp_path):
    """CE-13v. The single most likely implementation error: reading only
    ``dependencies`` loses this edge, and treating ``dependencies=[]`` as
    detachment reaches the opposite conclusion."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a], evidence=[a])

    second = revise(instance, b, {"dependencies": []}, mode="none", reason="clear deps")
    assert second["evidence_refs"] == [a], "evidence_refs is inherited, not cleared"

    retract(instance, a)
    rows = {row[1]: (row[2], row[3]) for row in impacts(instance)}
    assert second["ref"] in rows, "B@2 is still on A@1's basis"
    assert rows[second["ref"]] == (1, "evidence_refs"), "and the label says which field"

    third = revise(
        instance,
        second["ref"],
        {"dependencies": [], "evidence_refs": []},
        mode="none",
        reason="detach for real",
    )
    revise(instance, "knowledge/K-001@2", {}, mode="versions", scope=[a], reason="again")
    assert third["ref"] not in {row[1] for row in impacts(instance)}


def test_ce13a_the_narrow_protocol_accepts_and_refuses_as_specified(tmp_path):
    """CE-13a. grounded_in becomes the basis; the rest only declare."""
    instance = store(tmp_path)
    claim = record(instance, "claim")
    question = record(instance, "question", kind="open_question")

    grounded = instance.record_knowledge(
        {
            "kind": "decision",
            "statement": "grounded",
            "relations": [{"type": "grounded_in", "target": claim}],
        },
        "rel-grounded",
    )
    assert grounded["dependencies"] == [claim], "grounded_in merges into the basis"
    assert relations(instance) == [], "and is not stored a second time"

    answering = instance.record_knowledge(
        {
            "kind": "claim",
            "statement": "answer",
            "evidence_refs": [claim],
            "relations": [{"type": "answers", "target": question}],
        },
        "rel-answers",
    )
    assert relations(instance) == [(answering["ref"], question, "answers", "knowledge")]
    with instance._read() as db:
        status = db.execute(
            "SELECT status FROM knowledge_revisions WHERE knowledge_id=? AND revision=1",
            (question.split("/")[1].split("@")[0],),
        ).fetchone()[0]
    assert status == "proposed", "answering does not resolve the question"

    with pytest.raises(ValidationError, match="open_question"):
        instance.record_knowledge(
            {
                "kind": "decision",
                "statement": "bad answer",
                "relations": [{"type": "answers", "target": claim}],
            },
            "rel-bad-answers",
        )
    with pytest.raises(ValidationError, match="exact version"):
        instance.record_knowledge(
            {
                "kind": "decision",
                "statement": "bad target",
                "relations": [{"type": "answers", "target": "knowledge/K-002"}],
            },
            "rel-bad-target",
        )
    with pytest.raises(ValidationError, match="one of"):
        instance.record_knowledge(
            {
                "kind": "decision",
                "statement": "bad type",
                "relations": [{"type": "invented", "target": claim}],
            },
            "rel-bad-type",
        )


def test_ce13a_supersedes_may_not_be_a_self_loop(tmp_path):
    """CE-13a. Deterministically invalid input, refused rather than stored."""
    instance = store(tmp_path)
    a = record(instance, "a")
    with pytest.raises(ValidationError, match="supersede itself"):
        instance.revise_knowledge(
            {
                "ref": a,
                "expected_revision": 1,
                "changes": {"statement": "v2"},
                "reason": "self loop",
                "affected_scope_mode": "none",
                # K-001@2 is the version this very call would create.
                "relations": [{"type": "supersedes", "target": "knowledge/K-001@2"}],
            },
            "self-loop",
        )


def context_section(instance, name):
    """The coordinator reads rendered text, so assert against what it sees."""
    text = instance.context_view("host", "main")["text"]
    marker = f"## {name}\n"
    if marker not in text:
        return None
    body = text.split(marker, 1)[1]
    end = body.find("\n## ")
    return json.loads(body if end < 0 else body[:end])


def test_the_coordinator_context_shows_affected_entries_with_a_path(tmp_path):
    """R5/R20. The context consumes the same semantics as the checks, keeps
    risks and version notices apart, and reports what it folded away."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[b])
    retract(instance, a)

    section = context_section(instance, "affected_knowledge")
    assert section["total"] == 3
    assert {item["affected_version"] for item in section["items"]} == {a, b, c}
    assert section["targets_complete"] is True
    assert "sequence_bound" in section

    downstream = next(item for item in section["items"] if item["affected_version"] == c)
    assert downstream["hop"] == 2
    assert downstream["explanation"][-1]["to"] == a, "a path back to the change root"
    assert [reason["reason"] for reason in downstream["residual_use_risk"]] == [
        "in_change_scope"
    ]
    assert downstream["version_notices"] == [], "notices are a separate field"

    dispose(instance, "CH-001", c, "retained_with_evidence")
    reread = context_section(instance, "affected_knowledge")
    assert c not in {item["affected_version"] for item in reread["items"]}, "settled items drop out"


def test_the_context_counts_affected_entries_that_are_no_longer_in_use(tmp_path):
    """R18. Folding is not hiding: the count has to be visible."""
    instance = store(tmp_path)
    a = record(instance, "a")
    d = record(instance, "d", dependencies=[a])
    live = record(instance, "live", dependencies=[a])
    # D moves on and nothing cites D@1 any more; LIVE@1 stays the latest of its
    # entry, so it keeps A@1 reachable from an anchor.
    revise(instance, d, {"dependencies": []}, mode="none", reason="d moves on")
    retract(instance, a)

    section = context_section(instance, "affected_knowledge")
    by_version = {item["affected_version"]: item for item in section["items"]}
    assert by_version[live]["in_use"] is True
    assert by_version[a]["in_use"] is True, "still reachable through LIVE@1"
    assert by_version[d]["in_use"] is False, "not latest, not pinned, never cited"
    assert section["not_in_use_count"] == 1
    assert d in by_version, "folded from the default view, never dropped from the ledger"


# --- A1 复核返修 (2026-09-21) --------------------------------------------


def test_ce13a_both_relation_entries_write_without_colliding(tmp_path):
    """CE-13a (B1). The generic tool and the narrow protocol mint ids into one
    table, so they must share one counter."""
    instance = store(tmp_path)
    a = record(instance, "a")
    question = record(instance, "q", kind="open_question")
    b = record(instance, "b")

    instance.relate(b, a, "relates_to", "generic first", "generic-1")
    first = instance.record_knowledge(
        {"kind": "decision", "statement": "declared", "relations": [{"type": "answers", "target": question}]},
        "declared-1",
    )
    instance.relate(b, a, "relates_to", "generic second", "generic-2")
    second = instance.record_knowledge(
        {"kind": "decision", "statement": "declared again", "relations": [{"type": "answers", "target": question}]},
        "declared-2",
    )
    rows = relations(instance)
    assert len(rows) == 4
    with instance._read() as db:
        ids = [row[0] for row in db.execute("SELECT relation_id FROM relations")]
    assert len(set(ids)) == 4, "every relation id is distinct"
    assert first["ref"] != second["ref"]


def test_ce13a_record_refuses_change_event_parameters(tmp_path):
    """CE-13a (S5). They belong to revise; silently dropping them would lose a
    declaration the caller believed they had made."""
    instance = store(tmp_path)
    for parameter, value in (
        ("affected_scope_mode", "none"),
        ("affected_scope", ["knowledge/K-001@1"]),
        ("change_kind", "retract"),
    ):
        with pytest.raises(ValidationError, match="applies to revise"):
            instance.record_knowledge(
                {"kind": "decision", "statement": "bad", parameter: value}, f"bad-{parameter}"
            )


def test_ce13a_challenges_is_recorded_without_touching_the_target(tmp_path):
    """CE-13a. A challenge coexists with what it challenges."""
    instance = store(tmp_path)
    a = record(instance, "a")
    challenger = instance.record_knowledge(
        {"kind": "decision", "statement": "disagree", "relations": [{"type": "challenges", "target": a}]},
        "challenge",
    )
    assert relations(instance) == [(challenger["ref"], a, "challenges", "knowledge")]
    with instance._read() as db:
        status = db.execute(
            "SELECT status FROM knowledge_revisions WHERE knowledge_id='K-001' AND revision=1"
        ).fetchone()[0]
    assert status == "proposed", "challenging does not change the target"


def test_ce10c_a_publication_pinning_a_retracted_version_is_flagged(tmp_path):
    """CE-10c. The P-level RR1 comes from the pinned version's own status."""
    instance = store(tmp_path)
    a = record(instance, "a")
    retract(instance, a)
    publication = publish(instance, status="complete", refs=["knowledge/K-001@2"], op="pub-ce10c")

    assert publication["check"]["status"] == "attention"
    assert publication["check"]["flagged"] == ["knowledge/K-001@2"]
    check = instance.publication_check(publication["publication_id"])
    listed = check["result"]["versions"]["knowledge/K-001@2"]["residual_use_risk"]
    assert [item["reason"] for item in listed] == ["retracted_ref"]
    assert listed[0]["source"] == "knowledge/K-001@2"
    assert check["targets_complete"] is True


def test_ce8e_step4_a_late_reference_to_an_excluded_root_is_not_raised(tmp_path):
    """CE-8e step 4 (B2). Roots come from the effective scope, not from the
    hop-0 rows a narrowing deliberately left behind."""
    instance = store(tmp_path)
    a = record(instance, "a")
    revise(instance, a, {"statement": "second"}, mode="none", reason="grow")
    revise(instance, "knowledge/K-001@2", {}, mode="unknown", reason="uncertain")
    instance.narrow_impact_scope(
        {"change_id": "CH-002", "affected_scope": ["knowledge/K-001@2"], "reason": "narrow"},
        "narrow",
    )

    e = record(instance, "e", dependencies=[a])
    page = instance.knowledge_impacts(version=e)
    assert page["items"] == [], "A@1 is no longer a root of CH-002"
    assert risk(instance, e)["needs_action"] is False
    assert [n["notice"] for n in risk(instance, e)["version_notices"]] == [
        "newer_revision_exists"
    ]


def test_ce8e_step5_narrowing_is_repeatable_and_monotone(tmp_path):
    """CE-8e step 5 (S2). Eligibility is the original declaration; the subset
    basis is whatever is in force now."""
    instance = store(tmp_path)
    a = record(instance, "a")
    revise(instance, a, {"statement": "v2"}, mode="none", reason="grow")
    revise(instance, "knowledge/K-001@2", {"statement": "v3"}, mode="none", reason="grow")
    revise(instance, "knowledge/K-001@3", {}, mode="unknown", reason="uncertain")

    instance.narrow_impact_scope(
        {"change_id": "CH-003", "affected_scope": ["knowledge/K-001@1", "knowledge/K-001@2"], "reason": "first"},
        "narrow-1",
    )
    instance.narrow_impact_scope(
        {"change_id": "CH-003", "affected_scope": ["knowledge/K-001@1"], "reason": "second"},
        "narrow-2",
    )
    with instance._read() as db:
        assert db.execute("SELECT COUNT(*) FROM knowledge_scope_revisions").fetchone()[0] == 2
    with pytest.raises(ValidationError, match="not inside the scope"):
        instance.narrow_impact_scope(
            {"change_id": "CH-003", "affected_scope": ["knowledge/K-001@2"], "reason": "widen"},
            "narrow-3",
        )
    page = instance.knowledge_impacts(change_id="CH-003")
    assert {item["affected_version"] for item in page["items"]} == {"knowledge/K-001@1"}


def test_ce10r_a_retracted_version_is_neither_anchor_nor_target(tmp_path):
    """CE-10r (user adjudication). A withdrawn entry cannot be an action item;
    traversal still passes through it to reach whoever cites it."""
    instance = store(tmp_path)
    z = record(instance, "z")
    a = record(instance, "a", dependencies=[z])

    retract(instance, a, op="ce10r-a")
    assert [(row[1], row[2]) for row in impacts(instance)] == [(a, 0)]
    assert edges(instance)[("knowledge/K-002@2", z)] == (1, 0), "A@2 inherited the edge"
    assert risk(instance, z)["in_use"] is True

    retract(instance, z, op="ce10r-z")
    rows = [(row[0], row[1], row[2]) for row in impacts(instance)]
    # Sorted by (change_id, affected_version); z is K-001 and a is K-002.
    assert rows == [("CH-001", a, 0), ("CH-002", z, 0), ("CH-002", a, 1)]
    assert "knowledge/K-002@2" not in {row[1] for row in impacts(instance)}

    assert risk(instance, z)["in_use"] is False, "a retracted latest revision is not an anchor"
    assert risk(instance, a)["in_use"] is False
    assert risk(instance, a)["needs_action"] is True
    assert risk(instance, "knowledge/K-002@2")["needs_action"] is False
    assert reasons(instance, "knowledge/K-002@2") == []
    assert instance.impact_next() is None or instance.impact_next()[
        "affected_version"
    ] != "knowledge/K-002@2"


def test_ce10r_traversal_still_passes_through_a_retracted_version(tmp_path):
    """CE-10r. D@1 cites the retracted A@2 and is found at hop 2."""
    instance = store(tmp_path)
    z = record(instance, "z")
    a = record(instance, "a", dependencies=[z])
    retract(instance, a, op="ce10r2-a")
    d = record(instance, "d", dependencies=["knowledge/K-002@2"])
    retract(instance, z, op="ce10r2-z")

    rows = {row[1]: row[2] for row in impacts(instance)}
    assert rows.get(d) == 2, "reached through the retracted A@2"
    assert "knowledge/K-002@2" not in rows
    assert "retracted_ref" in reasons(instance, d)


def test_ce8f_no_impacts_but_an_uncovered_reference_is_not_a_pass(tmp_path):
    """CE-8f (B4). Reading completeness off the impact page alone calls this
    clear while the same receipt lists an uncovered reference."""
    instance = store(tmp_path)
    background = publish(instance, status="partial", refs=[], op="pub-000")
    k = record(instance, "k", dependencies=[f"pub/{background['publication_id']}"])
    publication = publish(instance, status="complete", refs=[k], op="pub-ce8f")

    check = instance.stored_publication_check(publication["publication_id"])
    assert check["targets_complete"] == 0
    assert check["result"]["status"] == "coverage partial"
    assert check["result"]["flagged"] == []
    assert [item["ref"] for item in check["result"]["versions"][k]["uncovered_refs"]] == [
        f"pub/{background['publication_id']}"
    ]


def test_ce8g_a_publication_check_reports_its_own_truncation(tmp_path):
    """CE-8g (B4). targets_truncated must not be hardcoded false."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    for index in range(51):
        revise(
            instance,
            f"knowledge/K-001@{index + 1}",
            {"statement": f"v{index + 2}"},
            mode="versions",
            scope=[a],
            reason=f"cause-{index}",
        )
    publication = publish(instance, status="complete", refs=[b], op="pub-ce8g")

    page = instance.knowledge_impacts(version=b)
    assert (page["total"], page["shown"]) == (51, 50)
    check = instance.stored_publication_check(publication["publication_id"])
    assert check["targets_truncated"] == 1
    assert check["targets_complete"] == 1
    assert risk(instance, b)["needs_action"] is True


def test_ce8h_completeness_does_not_move_with_the_page(tmp_path):
    """CE-8h (B4). It is a property of the set."""
    instance = store(tmp_path)
    publication = publish(instance, status="partial", refs=[], op="pub-ce8h")
    a = record(instance, "a")
    record(instance, "b", dependencies=[a, f"pub/{publication['publication_id']}"])
    retract(instance, a)

    narrow = instance.knowledge_impacts(change_id="CH-001", limit=1)
    full = instance.knowledge_impacts(change_id="CH-001")
    assert narrow["targets_complete"] is False
    assert narrow["targets_truncated"] is True
    assert full["targets_complete"] is False
    assert narrow["targets_complete"] == full["targets_complete"]


def test_ce2_impact_next_does_not_hand_out_a_prepared_proposal_again(tmp_path):
    """CE-2 (S1). Otherwise one item starves everything behind it."""
    instance = store(tmp_path)
    a = record(instance, "a")
    record(instance, "b", dependencies=[a])
    retract(instance, a)

    first = instance.impact_next()
    assert first is not None
    instance.impact_review_state(
        first["change_id"], first["affected_version"], "proposal_ready", "progress"
    )
    remaining = instance.impact_next()
    assert remaining is None or remaining["affected_version"] != first["affected_version"]

    # Re-reviewing is an explicit act.
    instance.impact_review_state(
        first["change_id"], first["affected_version"], "pending", "reopen"
    )
    assert instance.impact_next()["affected_version"] == first["affected_version"]


def test_ce14d_a_diamond_below_the_fork_still_reports_two_paths(tmp_path):
    """CE-14d (S3). Counting the target's own out-edges gives one and is wrong."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    c = record(instance, "c", dependencies=[a])
    d = record(instance, "d", dependencies=[b, c])
    e = record(instance, "e", dependencies=[d])

    retract(instance, a)
    page = instance.knowledge_impacts(version=e)
    assert page["items"][0]["hop"] == 3
    assert len(page["items"][0]["explanation"]) == 3, "one path, three edges"
    assert page["paths_truncated"] is True


def test_ce14_a_read_boundary_never_contains_a_future_write(tmp_path):
    """CE-14 supplement (S6). The read boundary is the last committed event;
    the write sequence is that plus one. They are different functions."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    retract(instance, a)

    first = instance.knowledge_impacts(change_id="CH-001")
    bound = first["sequence_bound"]
    dispose(instance, "CH-001", b, "retained_with_evidence")

    replay = instance.knowledge_impacts(change_id="CH-001", bound=bound)
    assert replay["items"] == first["items"], "the later disposition is not in the old view"
    assert replay["total"] == first["total"]
    assert (
        replay["targets_complete"],
        replay["targets_truncated"],
        replay["paths_truncated"],
    ) == (
        first["targets_complete"],
        first["targets_truncated"],
        first["paths_truncated"],
    )


def test_ce7_replaying_a_stored_check_reproduces_its_version_notices(tmp_path):
    """CE-7 (S6). A later rewording must not surface in the old snapshot."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    publication = publish(instance, status="complete", refs=[b], op="pub-ce7n")
    stored = instance.stored_publication_check(publication["publication_id"])
    assert stored["result"]["versions"][b]["version_notices"] == []

    revise(instance, a, {"statement": "reworded"}, mode="none", kind="reword", reason="wording")
    replay = instance.publication_check(publication["publication_id"], stored["sequence_bound"])
    assert replay["result"]["versions"][b]["version_notices"] == []
    current = instance.publication_check(publication["publication_id"])
    assert [n["notice"] for n in current["result"]["versions"][b]["version_notices"]] == [
        "newer_revision_exists"
    ]


def test_a_stored_check_does_not_freeze_review_progress(tmp_path):
    """S6. review_state is where a review got to, not what the check found."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    retract(instance, a)
    publication = publish(instance, status="complete", refs=[b], op="pub-state")
    stored = instance.stored_publication_check(publication["publication_id"])
    for item in stored["result"]["versions"][b]["impacts"]:
        assert "review_state" not in item


def test_ce12v_the_default_view_selects_before_it_pages(tmp_path):
    """CE-12v (S7). Paging first and filtering after reports a total nobody can
    reach: the settled rows eat the whole page."""
    instance = store(tmp_path)
    a = record(instance, "a")
    users = [record(instance, f"u{index}", dependencies=[a]) for index in range(10)]
    retract(instance, a)
    for version in [a, *users[:8]]:
        dispose(instance, "CH-001", version, "retained_with_evidence")

    section = context_section(instance, "affected_knowledge")
    assert section["total"] == 2
    assert section["shown_count"] == 2
    assert {item["affected_version"] for item in section["items"]} == set(users[8:])


def test_ce12v2_truncating_the_page_does_not_make_the_set_incomplete(tmp_path):
    """CE-12v-2 (R15). Completeness is a property of the whole open set. Folding
    the page length into it reports "incomplete" for every set above eight."""
    instance = store(tmp_path)
    a = record(instance, "a")
    for index in range(10):
        record(instance, f"u{index}", dependencies=[a])
    retract(instance, a)

    section = context_section(instance, "affected_knowledge")
    assert section["total"] == 11
    assert section["shown_count"] == 8
    assert section["targets_truncated"] is True
    assert section["targets_complete"] is True


def test_ce12v2_an_uncovered_ref_off_the_page_still_shows_as_incomplete(tmp_path):
    """CE-12v-2 variant (R15/R16). The one row carrying an uncovered reference
    is detected last, so it can never be on the first page: judging the flag
    from what is shown would call this set determined."""
    instance = store(tmp_path)
    publication = publish(instance, status="partial", refs=[], op="pub-ce12v2")
    a = record(instance, "a")
    users = [record(instance, f"u{index}", dependencies=[a]) for index in range(10)]
    retract(instance, a)
    late = record(
        instance,
        "late",
        dependencies=[users[0], f"pub/{publication['publication_id']}"],
    )

    section = context_section(instance, "affected_knowledge")
    shown = {item.get("affected_version") for item in section["items"]}
    assert late in {row["affected_version"] for row in instance.knowledge_impacts()["items"]}
    assert late not in shown
    assert section["targets_complete"] is False


def test_ce10b_the_context_lists_versions_citing_a_retracted_one(tmp_path):
    """CE-10b (S7). RR1 can hold with no impact record at all."""
    instance = store(tmp_path)
    a = record(instance, "a")
    retract(instance, a)
    d = record(instance, "d", evidence=["knowledge/K-001@2"])

    section = context_section(instance, "retracted_refs")
    assert section["total"] >= 1
    assert {item["version"] for item in section["items"]} == {d}
    assert section["items"][0]["retracted_ref"] == "knowledge/K-001@2"
    assert "shown_count" in section


def test_ce10b2_a_retracted_citer_is_not_an_action_item(tmp_path):
    """CE-10b-2 (R18/CE-10r). Retraction appends a revision that inherits the
    references, so the withdrawn citer keeps its edge to the retracted version.
    Listing it asks the reader to repair something already withdrawn."""
    instance = store(tmp_path)
    a = record(instance, "a")
    retract(instance, a)
    retracted = "knowledge/K-001@2"

    d = record(instance, "d", evidence=[retracted])
    retract(instance, d)  # D@2 is retracted and inherits the reference
    e = record(instance, "e", evidence=[retracted])
    revise(instance, e, {"evidence_refs": []}, mode="none", reason="e drops it")
    f = record(instance, "f", evidence=[retracted])

    section = context_section(instance, "retracted_refs")
    assert {item["version"] for item in section["items"]} == {d, e, f}
    assert section["total"] == 3
    # Not in use is still listed and counted, the same way the affected set
    # treats it: D@1 is behind a retracted latest, E@1 is a superseded old
    # revision nobody cites, F@1 is the latest.
    assert {item["version"]: item["in_use"] for item in section["items"]} == {
        d: False,
        e: False,
        f: True,
    }
    assert section["not_in_use_count"] == 2


def test_ce5_an_editorial_revision_leaves_no_risk_only_a_notice(tmp_path):
    """CE-5 (S4). The predicates the original test omitted."""
    instance = store(tmp_path)
    a = record(instance, "a")
    b = record(instance, "b", dependencies=[a])
    d = record(instance, "d", dependencies=[a])

    revise(instance, a, {"statement": "reworded"}, mode="none", kind="reword", reason="wording")
    for version in (b, d):
        assert risk(instance, version)["needs_action"] is False
        assert risk(instance, version)["residual_use_risk"] == []
        assert [n["notice"] for n in risk(instance, version)["version_notices"]] == [
            "newer_revision_exists"
        ]


def test_ce6_two_causes_on_one_target_are_both_reported(tmp_path):
    """CE-6 (S4). RR2 must name both change ids, not collapse them."""
    instance = store(tmp_path)
    a = record(instance, "a")
    d = record(instance, "d")
    retract(instance, a, op="ce6b-a")
    h = record(instance, "h", dependencies=[a, d])
    retract(instance, d, op="ce6b-d")

    assert risk(instance, h)["needs_action"] is True
    sources = sorted(
        item["change_id"]
        for item in risk(instance, h)["residual_use_risk"]
        if item["reason"] == "in_change_scope"
    )
    assert sources == ["CH-001", "CH-002"]


# --- A2 S14: frozen expectations CE-15 / CE-16 / CE-17 --------------------

from pathlib import Path
from auto_research.errors import NotFoundError
from auto_research.service import NativeService


def s14_call(service, method, operation, session="main", **fields):
    return service.handle(dict(transport_id=operation, operation_id=operation,
        host_id="host", session_id=session, method=method, **fields))["value"]


@pytest.fixture
def s14(tmp_path):
    service = NativeService(tmp_path / "registry.sqlite3")
    root = tmp_path / "project"
    s14_call(service, "open", "open", root=str(root), goal="CE-15–17")
    s14_call(service, "focus", "focus", role="planner", mode="manual")
    for name, text in {"REPORT.md": "original report\n", "out/a.txt": "a\n",
                       "out/sub/b.txt": "b original\n", "we ird.txt": "spaces\n"}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    s14_call(service, "publish", "pub", status="partial", summary="fixture", items=[
        {"item_id": "report", "source_path": "REPORT.md"},
        {"item_id": "bundle", "source_path": "out"},
        {"item_id": "note", "content": {"note": "content only"}}])
    s14_call(service, "snapshot", "s1", paths=["REPORT.md", "out", "we ird.txt"])
    s14_call(service, "snapshot", "s2", paths=["REPORT.md", "missing.txt", "REPORT.md"])
    instance = NativeStore(root)
    with instance._read() as db:
        version = db.execute("SELECT object_version FROM publication_items WHERE item_id='report'").fetchone()[0]
        snapshots = list(db.execute("SELECT complete FROM snapshots ORDER BY snapshot_id"))
        assert [row[0] for row in snapshots] == [1, 0]
    yield service, root, instance, root / ".research" / "objects" / version
    # Archives are read-only; restore directory permissions for pytest cleanup.
    for path in (root / ".research" / "objects").rglob("*"):
        if path.is_dir():
            path.chmod(0o755)


CE15 = [
 ("pub/P-001", "resolved", "publication", None),
 ("pub/P-001#report", "resolved", "publication_item", "file"),
 ("pub/P-001#note", "resolved", "publication_item", None),
 ("pub/P-001#bundle", "resolved", "publication_item", "directory"),
 ("pub/P-001#bundle/sub/b.txt", "resolved", "object_subpath", "directory"),
 ("pub/P-001#bundle/../REPORT.md", "unsupported", "path_escape", None),
 ("pub/P-001#report/x", "not_found", "subpath_missing", None),
 ("pub/P-001#bundle/zzz", "not_found", "subpath_missing", None),
 ("pub/P-001#nope", "not_found", "item_missing", None),
 ("pub/P-999", "not_found", "publication_missing", None),
 ("pub/P-001#", "unsupported", "bad_syntax", None),
 ("pub/", "unsupported", "bad_syntax", None),
 ("S-001", "resolved", "snapshot", None),
 ("S-001#REPORT.md", "resolved", "snapshot_entry", "file"),
 ("S-001#./out/", "resolved", "snapshot_entry", "directory"),
 ("S-001#out/sub/b.txt", "resolved", "object_subpath", "directory"),
 ("S-001#we%20ird.txt", "resolved", "snapshot_entry", "file"),
 ("S-001#missing.txt", "not_found", "entry_missing", None),
 ("S-001#out/zzz", "not_found", "subpath_missing", None),
 ("S-001#/REPORT.md", "unsupported", "path_escape", None),
 ("S-001#out/../REPORT.md", "unsupported", "path_escape", None),
 ("S-001#", "unsupported", "bad_syntax", None),
 ("S-999", "not_found", "snapshot_missing", None),
 ("S-002", "resolved", "snapshot", None),
 ("S-002#missing.txt", "unavailable", "entry_incomplete", None),
 ("S-002#REPORT.md", "ambiguous", "duplicate_path", None),
]


@pytest.mark.parametrize("ref,outcome,kind_or_reason,object_kind", CE15)
def test_ce15_resolution_table(s14, ref, outcome, kind_or_reason, object_kind):
    from auto_research import frozen_refs
    _, root, instance, _ = s14
    with instance._read() as db:
        result = frozen_refs.resolve(db, root, ref)
    assert result["ref"] == ref
    assert result["outcome"] == outcome
    assert result["kind" if outcome == "resolved" else "reason"] == kind_or_reason
    if outcome == "resolved":
        assert result["reason"] is None
        assert (result["object"]["kind"] if result["object"] else None) == object_kind
        assert result["integrity"] == "unverified"
        if kind_or_reason == "object_subpath":
            assert result["subpath"] == "sub/b.txt"
    else:
        assert result["integrity"] is None
        assert ref in result["message"]
        if kind_or_reason == "entry_incomplete":
            assert result["entry"]["error"] in result["message"]


def test_ce15_registration_preserves_original(s14):
    _, _, instance, _ = s14
    value = record(instance, "raw", evidence=["S-001#./out/"])
    assert instance.reference_query(value)["value"]["evidence_refs"] == ["S-001#./out/"]


def s14_error(call, error_type, reason):
    with pytest.raises(error_type) as caught:
        call()
    # R30: first message line is the original ref followed by the exact code.
    assert str(caught.value).split(": ", 1)[1].splitlines()[0] == reason


def s14_reader(s14, ref):
    service, root, instance, _ = s14
    node = s14_call(service, "propose", "reader-node", question="read", why_now="now",
                     plan="read", root_reason="fixture")
    task = s14_call(service, "specialist_create", "reader-task",
                     fields={"purpose": "review", "label": "fixture", "prompt": "read", "inputs": []})
    s14_call(service, "specialist_bind_child", "reader-bind",
              task_id=task["task_id"], child_session_id="expert")
    # Model historical declarations / references whose object later disappears.
    # Deliberately bypass today's registration so read validation is independently tested.
    with instance._connection() as db:
        db.execute("UPDATE nodes SET inputs=? WHERE node_id=?", (json.dumps([ref]), node["node_id"]))
        db.execute("UPDATE specialist_tasks SET inputs=? WHERE task_id=?", (json.dumps([ref]), task["task_id"]))
    return node["node_id"], lambda: s14_call(service, "specialist_read_input", "read", session="expert", input_id="input-1")


def test_ce16_five_steps_and_read_boundaries(s14, monkeypatch):
    from auto_research import frozen_refs
    from auto_research.artifacts import ArtifactStore
    service, root, instance, archive = s14
    original = archive.read_bytes()
    node, read = s14_reader(s14, "pub/P-001#report")
    archive.unlink()
    assert (root / "REPORT.md").read_bytes() == original
    with instance._read() as db:
        for ref in ("pub/P-001#report", "S-001#REPORT.md"):
            result = frozen_refs.resolve(db, root, ref)
            assert (result["outcome"], result["reason"]) == ("unavailable", "object_missing")
    for method in ("validate_branch_inputs", "prepare_branch"):
        s14_error(lambda: s14_call(service, method, method, node_id=node), ValueError, "object_missing")
    s14_error(read, ValueError, "object_missing")
    archive.mkdir()
    with instance._read() as db:
        result = frozen_refs.resolve(db, root, "pub/P-001#report")
        assert (result["outcome"], result["reason"]) == ("unavailable", "object_kind_mismatch")
    archive.rmdir()
    archive.write_text("tampered\n")
    with instance._read() as db:
        # No hidden digest scan is permitted at query/registration level.
        with monkeypatch.context() as m:
            m.setattr(ArtifactStore, "verify", lambda *a: pytest.fail("resolve computed digest"))
            result = frozen_refs.resolve(db, root, "pub/P-001#report")
            assert (result["outcome"], result["integrity"]) == ("resolved", "unverified")
        result, path = frozen_refs.open(db, root, "pub/P-001#report")
        assert (result["outcome"], result["reason"], path) == ("unavailable", "object_corrupted", None)
    s14_error(read, ValueError, "object_corrupted")
    for method in ("validate_branch_inputs", "prepare_branch"):
        s14_error(lambda: s14_call(service, method, method, node_id=node), ValueError, "object_corrupted")
    archive.write_bytes(original)
    archive.chmod(0o444)
    with instance._read() as db:
        result, path = frozen_refs.open(db, root, "pub/P-001#report")
        assert (result["outcome"], result["integrity"]) == ("resolved", "verified")
        assert path.read_bytes() == original
    assert read()["text"].encode() == original
    prepared = s14_call(service, "prepare_branch", "good-prepare", node_id=node)
    assert (Path(prepared["workspace"]) / prepared["inputs"][0]["path"]).read_bytes() == original


CE17 = [
 ("pub/P-001#report", "unavailable", "object_missing", "publication-item"),
 ("S-002#REPORT.md", "ambiguous", "duplicate_path", "snapshot-entry"),
 ("S-001#missing.txt", "not_found", "entry_missing", None),
 ("S-001#out/../x", "unsupported", "path_escape", None),
 ("S-001#out/sub/b.txt", "resolved", None, "object-subpath"),
 ("pub/P-001#note", "resolved", None, "publication-item"),
]


@pytest.mark.parametrize("ref,outcome,reason,kind", CE17)
def test_ce17_query(s14, ref, outcome, reason, kind):
    service, _, _, archive = s14
    archive.unlink()
    query = lambda: s14_call(service, "query", "query-ref", ref=ref)
    if outcome in ("not_found", "unsupported"):
        s14_error(query, NotFoundError if outcome == "not_found" else ValidationError, reason)
    else:
        result = query()
        assert result["kind"] == kind
        assert result["resolution"]["outcome"] == outcome
        assert result["resolution"]["reason"] == reason
        if ref == "pub/P-001#note":
            assert result["resolution"]["object"] is None


def s14_counts(instance):
    with instance._read() as db:
        return [db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("events", "knowledge_revisions", "relations", "nodes", "publications", "requests")]


@pytest.mark.parametrize("ref,outcome,reason,kind", CE17)
@pytest.mark.parametrize("entry", ["evidence_refs", "inputs", "item_knowledge_refs", "knowledge_refs", "source_ref", "target_ref"])
def test_ce17_registration(s14, ref, outcome, reason, kind, entry):
    service, _, instance, archive = s14
    node = s14_call(service, "propose", "reg-node", question="reg", why_now="now", plan="reg", root_reason="fixture")
    archive.unlink()
    if entry == "evidence_refs":
        write = lambda: record(instance, "registration", evidence=[ref])
    elif entry == "inputs":
        write = lambda: s14_call(service, "propose", "reg", question="q", why_now="now", plan="p", root_reason="fixture", inputs=[ref])
    elif entry == "knowledge_refs":
        write = lambda: s14_call(service, "publish", "reg", status="partial", summary="refs", items=[], knowledge_refs=[ref])
    elif entry == "item_knowledge_refs":
        write = lambda: s14_call(service, "publish", "reg", status="partial", summary="refs", items=[{"item_id": "note", "content": {}, "knowledge_refs": [ref]}])
    else:
        write = lambda: instance.relate(ref if entry == "source_ref" else node["node_id"],
                   ref if entry == "target_ref" else node["node_id"], "mentions", "fixture", "reg")
    before = s14_counts(instance)
    if entry in {"knowledge_refs", "item_knowledge_refs"}:
        publication_before = s14_publication_counts(instance)
        with pytest.raises(ValidationError) as caught:
            write()
        assert str(caught.value) == f"Invalid knowledge reference: {ref}"
        assert s14_publication_counts(instance) == publication_before
        assert s14_counts(instance) == before
        return
    if outcome != "resolved":
        s14_error(write, NotFoundError if outcome == "not_found" else ValidationError, reason)
        assert s14_counts(instance) == before
    else:
        value = write()
        if entry == "evidence_refs":
            saved = instance.reference_query(value)["value"][entry]
        elif entry == "inputs":
            saved = instance.reference_query(value["node_id"])["value"][entry]
        else:
            saved = [instance.reference_query(value["relation_id"])["value"][entry]]
        assert saved == [ref]


@pytest.mark.parametrize("ref,outcome,reason,kind", CE17)
def test_ce17_material_preparation(s14, ref, outcome, reason, kind):
    service, _, _, archive = s14
    node, _ = s14_reader(s14, ref)
    archive.unlink()
    for method in ("validate_branch_inputs", "prepare_branch"):
        run = lambda: s14_call(service, method, method, node_id=node)
        if outcome != "resolved":
            s14_error(run, ValueError, reason)
        else:
            result = run()
            item = result["inputs"][0]
            assert item["materialized"] == (kind == "object-subpath")
            if method == "prepare_branch" and item["materialized"]:
                assert (Path(result["workspace"]) / item["path"]).read_text() == "b original\n"


@pytest.mark.parametrize("ref,outcome,reason,kind", CE17)
def test_ce17_specialist_read(s14, ref, outcome, reason, kind):
    _, _, _, archive = s14
    _, read = s14_reader(s14, ref)
    archive.unlink()
    if outcome != "resolved" or ref == "pub/P-001#note":
        s14_error(read, ValueError, reason or "not_an_object")
    else:
        assert read()["text"] == "b original\n"


def test_ce17_registration_does_not_verify_digest(s14, monkeypatch):
    from auto_research.artifacts import ArtifactStore
    _, _, instance, archive = s14
    _, read = s14_reader(s14, "pub/P-001#report")
    archive.chmod(0o644)
    archive.write_text("tampered\n")
    with monkeypatch.context() as m:
        m.setattr(ArtifactStore, "verify", lambda *a: pytest.fail("registration computed digest"))
        ref = record(instance, "tampered", evidence=["pub/P-001#report"])
        assert instance.reference_query(ref)["value"]["evidence_refs"] == ["pub/P-001#report"]
    s14_error(read, ValueError, "object_corrupted")


def test_ce15_longest_prefix_first_hash_and_normalized_duplicates(s14):
    """R29 supplements: nested entries, literal #, percent decoding, duplicate aliases."""
    from auto_research import frozen_refs
    service, root, instance, _ = s14
    (root / "we#ird.txt").write_text("literal hash\n")
    s14_call(service, "snapshot", "nested", paths=["out", "out/sub", "we#ird.txt"])
    with instance._read() as db:
        result = frozen_refs.resolve(db, root, "S-003#out/sub/b.txt")
        assert result["outcome"] == "resolved"
        assert result["entry"]["source_path"] == "out/sub"
        assert result["subpath"] == "b.txt"
        for ref in ("S-003#we#ird.txt", "S-003#we%23ird.txt"):
            result = frozen_refs.resolve(db, root, ref)
            assert result["outcome"] == "resolved"
            assert result["kind"] == "snapshot_entry"
        for ref in ("S-001#out//sub/b.txt", "S-001#out/%2e%2e/REPORT.md", "pub/P-001#bundle//sub/b.txt"):
            result = frozen_refs.resolve(db, root, ref)
            assert (result["outcome"], result["reason"]) == ("unsupported", "path_escape")
    s14_call(service, "snapshot", "aliases", paths=["out", "./out/"])
    with instance._read() as db:
        result = frozen_refs.resolve(db, root, "S-004#out/sub/b.txt")
        assert (result["outcome"], result["reason"]) == ("ambiguous", "duplicate_path")


def test_ce16_directory_digest_and_frozen_only_subpath(s14):
    """Opening a subtree verifies its owner, including bytes outside that subtree."""
    from auto_research import frozen_refs
    _, root, instance, _ = s14
    with instance._read() as db:
        result = frozen_refs.resolve(db, root, "S-001#out/sub/b.txt")
        owner = root / ".research" / "objects" / result["object"]["version"]
        sibling = owner / "a.txt"
        sibling.chmod(0o644)
        sibling.write_text("tampered sibling\n")
        result = frozen_refs.resolve(db, root, "S-001#out/sub/b.txt")
        assert (result["outcome"], result["integrity"]) == ("resolved", "unverified")
        result, path = frozen_refs.open(db, root, "S-001#out/sub/b.txt")
        assert (result["outcome"], result["reason"], path) == ("unavailable", "object_corrupted", None)
        sibling.write_text("a\n")
        sibling.chmod(0o444)
        (root / "out/sub/b.txt").write_text("workspace changed\n")
        result, path = frozen_refs.open(db, root, "S-001#out/sub/b.txt")
        assert result["integrity"] == "verified"
        assert path.read_text() == "b original\n"


def test_ce17_discussion_materializes_snapshot_subpath(s14):
    service, _, _, archive = s14
    node = s14_call(service, "propose", "discussion-node", question="q", why_now="now",
                     plan="p", root_reason="fixture", inputs=["S-001#out/sub/b.txt"])
    prepared = s14_call(service, "discussion_prepare", "discussion", node_id=node["node_id"])
    index = prepared["context"]["material_index"]
    assert index[0]["materialized"] is True
    assert (Path(prepared["workspace"]) / index[0]["path"]).read_text() == "b original\n"


# CE-17 corrections / three supplemental rows, S14 F1–F3 (2026-09-23).
def s14_publication_counts(instance):
    with instance._read() as db:
        return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("events", "publications", "publication_items", "publication_knowledge")}


@pytest.mark.parametrize("entry", ["knowledge_refs", "item_knowledge_refs"])
def test_ce17_f1_exact_knowledge_versions_still_publish(s14, entry):
    service, _, instance, _ = s14
    ref = record(instance, "publication-knowledge")
    item = {"item_id": "note", "content": {"value": "claim"}}
    fields = {"knowledge_refs": [ref]} if entry == "knowledge_refs" else {}
    if entry == "item_knowledge_refs":
        item["knowledge_refs"] = [ref]
    published = s14_call(service, "publish", "knowledge-publication", status="partial",
                         summary="claim", items=[item], **fields)
    pid = published["publication_id"]
    kid, rev = ref.removeprefix("knowledge/").split("@")
    with instance._read() as db:
        rows = list(db.execute("SELECT knowledge_id,revision FROM publication_knowledge WHERE publication_id=?", (pid,)))
        assert [(r["knowledge_id"], r["revision"]) for r in rows] == [(kid, int(rev))]
        event = db.execute("SELECT data FROM events WHERE kind='publication.created' ORDER BY event_id DESC LIMIT 1").fetchone()
        assert "knowledge_refs" not in json.loads(event["data"])
    value = instance.reference_query(f"pub/{pid}")["value"]
    assert "knowledge_refs" not in value  # no event-based reconstruction
    assert value["items"][0]["knowledge_refs"] == ([ref] if entry == "item_knowledge_refs" else [])


def s14_mixed_discussion_node(s14):
    service, _, instance, _ = s14
    note = s14_call(service, "note", "discussion-note", body="context only")
    with instance._read() as db:
        attempt = db.execute("SELECT attempt_id FROM attempts ORDER BY rowid LIMIT 1").fetchone()[0]
    refs = [note["note_id"], attempt, "pub/P-001#report", "S-001#out/sub/b.txt"]
    node = s14_call(service, "propose", "mixed-discussion-node", question="q", why_now="now",
                     plan="discuss", root_reason="fixture", inputs=refs)
    return node, refs


def test_ce17_f2_discussion_ignores_note_and_attempt(s14):
    service, _, _, _ = s14
    node, refs = s14_mixed_discussion_node(s14)
    result = s14_call(service, "discussion_prepare", "mixed-discussion", node_id=node["node_id"])
    index = {item["ref"]: item for item in result["context"]["material_index"]}
    assert all(ref not in index for ref in refs[:2])
    for ref, original in [(refs[2], "original report\n"), (refs[3], "b original\n")]:
        assert index[ref]["materialized"] is True
        assert (Path(result["workspace"]) / index[ref]["path"]).read_text() == original


@pytest.mark.parametrize("failure", ["object_missing", "object_corrupted"])
def test_ce17_f2_discussion_still_rejects_unreadable_objects(s14, failure):
    service, _, _, archive = s14
    node, _ = s14_mixed_discussion_node(s14)
    if failure == "object_missing":
        archive.unlink()
    else:
        archive.chmod(0o644)
        archive.write_text("tampered\n")
        archive.chmod(0o444)
    s14_error(lambda: s14_call(service, "discussion_prepare", "bad-discussion", node_id=node["node_id"]),
              ValueError, failure)


@pytest.mark.parametrize("method", ["validate_branch_inputs", "prepare_branch"])
def test_ce17_f3_whole_snapshot_is_not_branch_material(s14, method):
    service, _, _, _ = s14
    node = s14_call(service, "propose", "whole-snapshot-node", question="q", why_now="now",
                     plan="p", root_reason="fixture", inputs=["S-001"])
    with pytest.raises(ValueError) as caught:
        s14_call(service, method, "whole-snapshot-branch", node_id=node["node_id"])
    assert str(caught.value) == "Research reference cannot be used as an input: S-001 (snapshot)"


def test_ce17_f3_discussion_ignores_whole_snapshot(s14):
    service, _, _, _ = s14
    node = s14_call(service, "propose", "whole-snapshot-node", question="q", why_now="now",
                     plan="p", root_reason="fixture", inputs=["S-001"])
    result = s14_call(service, "discussion_prepare", "whole-snapshot-discussion", node_id=node["node_id"])
    assert result["context"]["node"]["inputs"] == ["S-001"]
    assert result["context"]["material_index"] == []

# S6 / CE-18: expectations frozen before implementation.
@pytest.mark.parametrize('case', ['branches_from', 'revises', 'depends_on', 'mismatched_inputs', 'mixed', 'has_predecessor'])
def test_ce18_lineage_without_material(tmp_path, case):
    service = NativeService(tmp_path / "registry.sqlite3")
    project_root = tmp_path / "project"
    s14_call(service, "open", "ce18-open", root=str(project_root), goal="CE-18")
    instance = NativeStore(project_root)
    root = s14_call(service, 'propose', 'ce18-root', question='root', why_now='now', plan='p', root_reason='independent')
    s14_call(service, "focus", "ce18-focus", node_id=root["node_id"], role="core", mode="manual")
    (project_root / "REPORT.md").write_text("predecessor report")
    s14_call(service, "publish", "ce18-publish", status="partial", summary="result",
              items=[{"item_id": "report", "source_path": "REPORT.md"}])
    kind = case if case in {'branches_from', 'revises', 'depends_on'} else 'branches_from'
    predecessors = [dict(node_id=root['node_id'], relation_type=kind, rationale='r', input_refs=[])]
    fields = {}
    if case == 'mismatched_inputs':
        fields['inputs'] = ['pub/P-001#report']
    if case == 'mixed':
        predecessors.append(dict(node_id=root['node_id'], relation_type='depends_on', rationale='r', input_refs=['pub/P-001#report']))
    def propose():
        return s14_call(service, 'propose', 'ce18-derived', question=f"follow {root['node_id']}", why_now='now', plan='p', predecessors=predecessors, **fields)
    if case in {'depends_on', 'mismatched_inputs'}:
        with pytest.raises(ValidationError) as error:
            propose()
        assert str(error.value) == ('predecessor input_refs must be nonempty reference lists' if case == 'depends_on' else 'inputs must exactly match predecessor input_refs')
        return
    node = propose()
    saved = instance.reference_query(node['node_id'])['value']
    assert saved['origin_kind'] == 'derived'
    assert saved['inputs'] == (['pub/P-001#report'] if case == 'mixed' else [])
    with instance._read() as db:
        rows = list(db.execute('SELECT * FROM node_dependencies WHERE successor_node_id=?', (node['node_id'],)))
        assert len(rows) == (2 if case == 'mixed' else 1)
        assert {row['relation_type'] for row in rows} == ({'branches_from','depends_on'} if case == 'mixed' else {kind})
        if case == 'has_predecessor':
            # CE-18 row 6: assert the no-predecessor prerequisite is false;
            # the actual S8 candidate consumer remains outside this slice.
            candidate_without_predecessor = db.execute('SELECT NOT EXISTS(SELECT 1 FROM node_dependencies WHERE successor_node_id=?)', (node['node_id'],)).fetchone()[0]
            assert candidate_without_predecessor == 0

# --- A2 S7: CE-19, CE-20, CE-23 -----------------------------------------
def s7_record(service, op, **fields):
    return s14_call(service, 'memory_write', op, action='record', fields={
        'kind': 'decision', 'statement': op, 'visibility': 'project', **fields})


@pytest.mark.parametrize('case', ['turn', 'bound_turn', 'no_turn', 'forged', 'self_report', 'revise', 'relate', 'publish', 'propose', 'relations', 'grounded_in'])
def test_ce19_asserted_at(s14, case):
    service, root, instance, _ = s14
    expected = {'host_id': 'host', 'session_id': 'main', 'turn': 3, 'operation_id': 'ce19'}
    if case == 'bound_turn':
        s14_call(service, 'bind_turn', 'bind5', turn=5)
        value = s7_record(service, 'ce19')
        expected['turn'] = 5
    elif case == 'no_turn':
        value = s7_record(service, 'ce19')
        expected['turn'] = None
    elif case == 'forged':
        before = s14_counts(instance)
        with pytest.raises(ValidationError):
            s7_record(service, 'ce19', asserted_at={'session_id': 'fake'})
        assert s14_counts(instance) == before
        return
    elif case == 'self_report':
        value = s7_record(service, 'ce19', source_identity={'session_id': 'fake'})
        assert value['source_identity'] == {'session_id': 'fake'}
        expected['turn'] = None
    elif case == 'revise':
        old = s7_record(service, 'old')
        old_source = old['asserted_at']
        instance.associate('host', 'other', 'associate-other')
        # Bind registry routing as well, without registering other as main.
        service.registry.register(instance.control_state()['project']['project_id'], root, 'host', 'other')
        value = s14_call(service, 'memory_write', 'ce19', session='other', turn=3,
            action='revise', fields={'ref': old['ref'], 'changes': {'statement': 'new'},
            'reason': 'edit', 'affected_scope_mode': 'none', 'change_kind': 'reword'})
        expected['session_id'] = 'other'
        assert instance.reference_query(old['ref'])['value']['asserted_at'] == old_source
    elif case == 'relate':
        a, b = s7_record(service, 'a'), s7_record(service, 'b')
        value = s14_call(service, 'relate', 'ce19', turn=3, source_ref=a['ref'], target_ref=b['ref'], label='related', note='test')
        with instance._read() as db:
            value = {'asserted_at': json.loads(db.execute('SELECT asserted_at FROM relations WHERE relation_id=?', (value['relation_id'],)).fetchone()[0])}
    elif case == 'publish':
        value = s14_call(service, 'publish', 'ce19', turn=3, status='partial', summary='test', items=[])
        value = instance.reference_query('pub/' + value['publication_id'])['value']
    elif case == 'propose':
        node = s14_call(service, 'propose', 'ce19', turn=3, question='question', why_now='now', plan='plan', root_reason='test')
        with instance._read() as db:
            row = db.execute('SELECT knowledge_id,revision FROM node_questions WHERE node_id=?', (node['node_id'],)).fetchone()
        value = instance.reference_query(f'knowledge/{row[0]}@{row[1]}')['value']
    elif case in {'relations', 'grounded_in'}:
        target = s7_record(service, 'target')
        value = s14_call(service, 'memory_write', 'ce19', turn=3, action='record', fields={
            'kind':'decision', 'statement':'relation', 'visibility':'project',
            'relations':[{'type':'complements' if case == 'relations' else 'grounded_in', 'target':target['ref']}]})
        with instance._read() as db:
            rows = db.execute('SELECT * FROM relations WHERE source_ref=?', (value['ref'],)).fetchall()
        if case == 'grounded_in':
            assert rows == []
            assert value['dependencies'] == [target['ref']]
        else:
            assert len(rows) == 1
            assert json.loads(rows[0]['asserted_at']) == value['asserted_at']
            with instance._read() as db:
                stored = db.execute('SELECT asserted_at FROM knowledge_revisions WHERE knowledge_id=? AND revision=?', (value['knowledge_id'], value['revision'])).fetchone()[0]
            assert rows[0]['asserted_at'] == stored
    else:
        value = s14_call(service, 'memory_write', 'ce19', turn=3, action='record', fields={
            'kind':'decision', 'statement':'test', 'visibility':'project'})
    assert value['asserted_at'] == expected


@pytest.mark.parametrize('case', ['linked', 'unlinked', 'omitted', 'invalid', 'persistent'])
def test_ce20_execution_refs(s14, case):
    service, root, instance, archived = s14
    linked = ['session:main','attempt:A-001','pub/P-001#report','S-001#REPORT.md','event:1']
    unlinked = ['session:ghost','attempt:A-999','pub/P-001#note','tool_call:abc','S-001#missing.txt','S-001#out/../x']
    if case == 'invalid':
        before = s14_counts(instance)
        with pytest.raises(ValidationError):
            s7_record(service, 'execution', execution_refs='session:main')
        assert s14_counts(instance) == before
        return
    refs = unlinked if case == 'unlinked' else linked
    value = s7_record(service, 'execution', **({} if case == 'omitted' else {'execution_refs': refs}))
    expected = [] if case == 'omitted' else [dict(ref=ref,status='unlinked' if case=='unlinked' else 'linked',reason=reason) for ref,reason in zip(refs,
        ['not_found','not_found','not_an_object','unsupported_form','entry_missing','path_escape'] if case=='unlinked' else [None]*5)]
    assert value['execution_refs'] == expected
    if case == 'persistent':
        archived.parent.chmod(0o755)
        archived.chmod(0o755)
        import shutil
        shutil.rmtree(archived) if archived.is_dir() else archived.unlink()
    assert instance.reference_query(value['ref'])['value']['execution_refs'] == expected


def schema7_fixture(instance):
    with sqlite3.connect(instance.db_path) as db:
        for table, column in [('knowledge_revisions','asserted_at'), ('knowledge_revisions','execution_refs'), ('relations','asserted_at'), ('publications','asserted_at')]:
            if column in {row[1] for row in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
        db.execute('DROP TABLE IF EXISTS field_checks')
        if 'checks' in {r[1] for r in db.execute('PRAGMA table_info(knowledge_revisions)')}:
            db.execute('ALTER TABLE knowledge_revisions DROP COLUMN checks')
        db.execute('PRAGMA user_version=7')
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.close()


@pytest.mark.parametrize('case', ['backup', 'rollback', 'columns', 'history', 'old_package', 'idempotent'])
def test_ce23_schema8(s14, monkeypatch, case):
    service, root, instance, _ = s14
    prior = s7_record(service, 'historical')
    a = s7_record(service, 'historical-target')
    instance.relate(prior['ref'], a['ref'], 'related', 'old', 'old-relation')
    schema7_fixture(instance)
    original = instance.db_path.read_bytes()
    from auto_research import schema8
    backup = instance.meta / 'schema-7-backup.sqlite3'
    if case == 'rollback':
        def fail(db):
            db.execute("ALTER TABLE knowledge_revisions ADD COLUMN asserted_at TEXT NOT NULL DEFAULT 'null'")
            raise RuntimeError('injected migration failure')
        monkeypatch.setattr(schema8, 'ensure_schema8_columns', fail)
        with pytest.raises(RuntimeError):
            schema8.migrate_schema8(instance.db_path)
        with sqlite3.connect(instance.db_path) as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 7
            for table in ['knowledge_revisions','relations','publications']:
                assert 'asserted_at' not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        return
    schema8.migrate_schema8(instance.db_path)
    upgraded = instance
    with upgraded._read() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 8
        for table in ['knowledge_revisions','relations','publications']:
            assert 'asserted_at' in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            assert all(r[0] == 'null' for r in db.execute(f'SELECT asserted_at FROM {table}'))
        assert {r[0] for r in db.execute('SELECT execution_refs FROM knowledge_revisions')} == {'[]'}
    with upgraded._read() as db:
        row=db.execute('SELECT e.kind,e.node_id,r.* FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id) WHERE knowledge_id=? AND revision=1',(prior['knowledge_id'],)).fetchone()
        assert upgraded._decode_knowledge(row)['asserted_at'] is None
    if case == 'backup':
        assert backup.read_bytes() == original
        backup.write_bytes(b'preserve-existing-backup')
        schema7_fixture(upgraded)
        schema8.migrate_schema8(instance.db_path)
        assert backup.read_bytes() == b'preserve-existing-backup'
    elif case == 'old_package':
        import auto_research.native_store as native
        monkeypatch.setattr(native, 'SCHEMA_VERSION', 7)
        with pytest.raises(ValidationError) as caught:
            NativeStore(root)
        assert '8' in str(caught.value)
    elif case == 'idempotent':
        before = instance.db_path.read_bytes()
        schema8.migrate_schema8(instance.db_path)
        assert instance.db_path.read_bytes() == before

@pytest.mark.parametrize('origin', ['legacy_import', 'schema3_backfill'])
def test_ce19_historical_questions_have_no_assertion(tmp_path, origin):
    if origin == 'legacy_import':
        from auto_research.store import Store
        from auto_research.migration import migrate_copy
        old = Store(tmp_path / 'legacy')
        old.initialize('historical question', budget=100)
        old.propose({'question':'Q-001', 'why_now':'now', 'plan':'read', 'inputs':[]})
        migrate_copy(old.root, tmp_path / 'imported')
        instance = NativeStore(tmp_path / 'imported')
    else:
        instance = store(tmp_path)
        instance.propose('historical question', 'now', 'plan', 'old-question')
        with sqlite3.connect(instance.db_path) as db:
            for table in ['knowledge_entries','knowledge_revisions','node_questions','node_checkpoints','publication_knowledge','knowledge_search','context_packs','context_requests','specialist_tasks','specialist_bindings','guidance_sources','review_todos']:
                db.execute(f'DROP TABLE {table}')
            db.execute('DROP TABLE IF EXISTS knowledge_fts')
            db.execute('UPDATE nodes SET question_ref=NULL')
            db.execute('PRAGMA user_version=3')
        instance = NativeStore(instance.root)
    with instance._read() as db:
        rows = db.execute('SELECT knowledge_id,revision FROM node_questions').fetchall()
    assert rows
    for row in rows:
        value = instance.reference_query(f'knowledge/{row[0]}@{row[1]}')['value']
        assert value['asserted_at'] is None
        assert value['execution_refs'] == []


@pytest.mark.parametrize('action', ['record', 'revise'])
@pytest.mark.parametrize('forgery', [None, {'session_id':'fake'}])
def test_ce19_server_field_rejected_even_null(s14, action, forgery):
    service, _, instance, _ = s14
    old = s7_record(service, 'old')
    fields = {'kind':'decision','statement':'test','visibility':'project','asserted_at':forgery} if action=='record' else {
        'ref':old['ref'],'changes':{'statement':'edit'},'reason':'edit', 'affected_scope_mode':'none','change_kind':'reword','asserted_at':forgery}
    before = s14_counts(instance)
    with pytest.raises(ValidationError):
        s14_call(service, 'memory_write', 'forged', action=action, fields=fields)
    assert s14_counts(instance) == before


def test_ce20_revise_resolves_execution_in_transaction(s14):
    service, _, instance, _ = s14
    old = s7_record(service, 'old')
    value = s14_call(service, 'memory_write', 'revise-execution', action='revise', fields={
        'ref':old['ref'],'changes':{'statement':'edit'},'reason':'edit', 'affected_scope_mode':'none','change_kind':'reword',
        'execution_refs':['attempt:A-001','S-001#missing.txt']})
    assert value['execution_refs'] == [dict(ref='attempt:A-001',status='linked',reason=None), dict(ref='S-001#missing.txt',status='unlinked',reason='entry_missing')]
    assert instance.reference_query(old['ref'])['value']['execution_refs'] == []


def test_ce19_offline_new_writes_have_tool_identity(tmp_path):
    instance = store(tmp_path)
    value = instance.record_knowledge({'kind':'decision','statement':'offline'}, 'offline')
    assert value['asserted_at'] == dict(host_id='native_store',session_id='native_store',turn=None,operation_id='offline')


def test_ce20_revise_omission_does_not_inherit_execution(s14):
    service, _, instance, _ = s14
    refs = ['session:main', 'attempt:A-001', 'pub/P-001#report', 'S-001#REPORT.md', 'event:1']
    old = s7_record(service, 'before-edit', execution_refs=refs)
    expected = [dict(ref=ref, status='linked', reason=None) for ref in refs]
    value = s14_call(service, 'memory_write', 'omit-execution', action='revise', fields={
        'ref':old['ref'], 'changes':{'statement':'edited'}, 'reason':'wording',
        'affected_scope_mode':'none', 'change_kind':'reword'})
    assert value['execution_refs'] == []
    assert instance.reference_query(value['ref'])['value']['execution_refs'] == []
    assert instance.reference_query(old['ref'])['value']['execution_refs'] == expected
    assert old['execution_refs'] == expected

# S8 / CE-21, rewritten contract of 2026-09-23. No historical replay promise.
def ce21_hint(klass, target, evidence, repair=()):
    return dict(class_=klass, target=target, evidence=evidence,
                candidate=True, confidence='candidate', repair_evidence=list(repair))


def ce21_expected(a, b, b2=None, relation=None, publication=False):
    rows = [ce21_hint('prose_mention_without_relation', b,
                     {'mention':a.split('/')[1].split('@')[0], 'field':'statement', 'candidate_versions':[a]}, [b2] if b2 else [])]
    if not relation:
        rows.append(ce21_hint('prose_mention_without_relation', b, {'mention':'X-002','field':'statement'}))
    rows.append(ce21_hint('whole_snapshot_evidence', b, {'ref':'S-001'}, [b2] if b2 else []))
    rows.append(ce21_hint('lineage_mention_without_predecessor', 'X-002',
                         {'mention':'X-001','field':'root_reason'}, [relation] if relation else []))
    if publication:
        rows.append(ce21_hint('complete_publication_cites_risk', 'pub/P-002', {'status':'attention','flagged':[a]}))
    return [{('class' if key=='class_' else key):value for key,value in row.items()} for row in rows]


def ce21_setup(s14, phase):
    service, _, instance, _ = s14
    node = s14_call(service, 'propose', 's8-root', question='question', why_now='now', plan='plan', root_reason='independent')
    assert node['node_id']=='X-001' and node['question_ref']=='knowledge/K-001@1'
    def add(op, statement, kind='decision', deps=(), ev=()):
        return s14_call(service,'memory_write',op,action='record',fields={
            'kind':kind,'statement':statement,'node_id':node['node_id'],
            'dependencies':list(deps),'evidence_refs':list(ev)})
    a=add('s8-a','base')['ref']
    b=add('s8-b',f"Builds on {a.split('/')[1].split('@')[0]} and X-001 and X-002",'claim',ev=['S-001'])['ref']
    c=add('s8-c',f'Builds on {a}','claim',deps=[a],ev=['S-001#REPORT.md'])['ref']
    d=add('s8-d','see K-005 again')['ref']
    assert [a,b,c,d]==[f'knowledge/K-{i:03d}@1' for i in range(2,6)]
    node2=s14_call(service,'propose','s8-root2',question='question',why_now='now',plan='plan',root_reason='continues X-001 after the handoff failed')
    assert node2['node_id']=='X-002' and node2['question_ref']=='knowledge/K-006@1'
    pub=s14_call(service,'publish','s8-pub',status='complete',summary='complete',items=[],knowledge_refs=[a])
    assert pub['publication_id']=='P-002'
    # CE-21 2026-09-23: bare snapshots on lesson/decision are not class-2 hints.
    e=add('s8-e','operational lesson','lesson',ev=['S-001'])['ref']
    f=add('s8-f','plain decision','decision',ev=['S-001'])['ref']
    assert [e,f]==['knowledge/K-007@1','knowledge/K-008@1']
    b2=relation=change=None
    if phase>=2:
        relation=instance.relate('X-001','X-002','lineage_correction','repair','s8-relate')['relation_id']
        assert relation=='R-001'
        b2=revise(instance,b,{'dependencies':[a],'evidence_refs':['S-001#REPORT.md']},mode='none',kind='reword')['ref']
        # CE-21 corrected: B's scope=none revision already wrote CH-001.
        change=retract(instance,a)['change']['change_id']
        assert change=='CH-002'
    if phase==3:
        dispose(instance,change,a,'retained_with_evidence')
    return a,b,b2,relation


def ce21_counts(instance):
    with instance._read() as db:
        return {table:db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ['events','relations','knowledge_revisions','node_dependencies']}


@pytest.mark.parametrize('phase',[1,2,3])
def test_ce21_three_reads(s14,phase):
    service,_,instance,_=s14
    a,b,b2,relation=ce21_setup(s14,phase)
    before=ce21_counts(instance)
    page=s14_call(service,'query',f's8-read-{phase}',collection='hints',limit=100)
    # Assert each added target separately before comparing the whole page.
    for target in ['knowledge/K-007@1','knowledge/K-008@1']:
        assert [item for item in page['items'] if item['target']==target]==[]
    expected=ce21_expected(a,b,b2,relation,publication=phase==2)
    assert page['items']==expected
    assert page['total']==(3 if phase==3 else 4)
    assert page['shown_count']==len(expected)
    assert all(item['candidate'] is True for item in page['items'])
    assert '诊断' not in json.dumps(page,ensure_ascii=False)
    absent=['knowledge/K-001@1',a,'knowledge/K-004@1','knowledge/K-005@1','knowledge/K-006@1','X-001']
    if b2:absent.append(b2)
    if phase!=2:absent.append('pub/P-002')
    assert not any(item['target'] in absent for item in page['items'])
    context=context_section(instance,'structure_hints')
    assert context['items']==expected and context['total']==len(expected)
    assert ce21_counts(instance)==before


@pytest.mark.parametrize('entry',['query','history_page','context','summary'])
def test_ce21_paging_selects_and_sorts_before_slice(s14,entry):
    service,_,instance,_=s14
    refs=[s7_record(service,f'hint-{i}',kind='claim',statement='plain statement',evidence_refs=['S-001'])['ref'] for i in range(11)]
    before=ce21_counts(instance)
    if entry=='context':
        page=context_section(instance,'structure_hints')
    elif entry=='summary':
        page=s14_call(service,'query','s8-summary')['structure_hints']
    else:
        page=s14_call(service,entry,'s8-page',collection='hints',limit=4,offset=8)
    assert page['total']==11
    assert page['shown_count']==(8 if entry in {'context','summary'} else 3)
    expected = sorted(refs)[:8] if entry in {'context','summary'} else sorted(refs)[8:]
    assert [item['target'] for item in page['items']] == expected
    assert ce21_counts(instance)==before


@pytest.mark.parametrize('field',['statement','scope','conditions'])
def test_ce21_evidence_refs_count_as_basis(s14,field):
    service,_,instance,_=s14
    a=s7_record(service,'evidence-a')['ref']
    mention=a.split('/')[1]
    fields={field:mention if field=='statement' else {'mentions':mention}}
    b=s7_record(service,'evidence-b',evidence_refs=[a],**fields)
    page=s14_call(service,'query','evidence-hints',collection='hints')
    assert [item for item in page['items'] if item['target']==b['ref']]==[]


def test_ce21_unversioned_mention_uses_revision_time(s14):
    service,_,instance,_=s14
    a=s7_record(service,'time-a')['ref']
    b=s7_record(service,'time-b',statement=a.split('/')[1].split('@')[0])['ref']
    revise(instance,a,{'statement':'later'},mode='none',kind='reword')
    page=s14_call(service,'query','time-hints',collection='hints')
    row=next(item for item in page['items'] if item['target']==b)
    assert row['evidence']['candidate_versions']==[a]


def test_ce21_publication_hint_reuses_publication_check(s14,monkeypatch):
    from auto_research import epistemic
    service,_,_,_=s14
    a,_,_,_=ce21_setup(s14,2)
    original=epistemic.publication_check
    called=[]
    def check(db,publication_id,*args,**kwargs):
        called.append(publication_id)
        return original(db,publication_id,*args,**kwargs)
    monkeypatch.setattr(epistemic,'publication_check',check)
    page=s14_call(service,'query','reuse-check',collection='hints')
    assert called==['P-002']
    assert next(item for item in page['items'] if item['target']=='pub/P-002')['evidence']=={'status':'attention','flagged':[a]}

@pytest.mark.parametrize('field',['statement','scope','conditions'])
def test_ce21_text_fields_emit_candidates(s14,field):
    service,_,_,_=s14
    a=s7_record(service,'field-a')['ref']
    mention=a.split('/')[1]
    b=s7_record(service,'field-b',**{field:mention if field=='statement' else {'mention':mention}})['ref']
    page=s14_call(service,'query','field-hints',collection='hints')
    assert page['total']==1
    assert page['items'][0]['target']==b
    assert page['items'][0]['evidence']=={'mention':mention,'field':field,'candidate_versions':[a]}


@pytest.mark.parametrize('form',['entry','original_revision','later_revision'])
def test_ce21_node_relation_covers_all_entry_reference_forms(s14,form):
    service,_,instance,_=s14
    a,b,b2,_=ce21_setup(s14,1)
    later=revise(instance,b,{'statement':'Builds on X-002'},mode='none',kind='reword')['ref']
    endpoint=later if form=='later_revision' else b
    relation=instance.relate('X-002',endpoint,'context','connected','entry-connection')
    if form=='entry':
        # Historical rows may use the entry form although today's narrow write
        # interface requires exact versions. R38 explicitly includes those rows.
        with instance._connection() as db:
            db.execute('UPDATE relations SET target_ref=? WHERE relation_id=?',
                       (b.split('@')[0],relation['relation_id']))
    page=s14_call(service,'query','entry-form-hints',collection='hints')
    assert not any(item['class']=='prose_mention_without_relation'
                   and item['target'] in {b,later} and item['evidence']['mention']=='X-002'
                   for item in page['items'])


def test_ce21_lineage_excludes_self_and_nodes_with_predecessors(s14):
    service,_,_,_=s14
    root=s14_call(service,'propose','self-node',question='question',why_now='now',plan='plan',root_reason='independent X-001')
    child=s14_call(service,'propose','derived-node',question='question',why_now='now',plan='continues X-001',predecessors=[{
        'node_id':root['node_id'],'relation_type':'branches_from','input_refs':[],'rationale':'continues'}])
    assert child['origin_kind']=='derived'
    page=s14_call(service,'query','lineage-self-hints',collection='hints')
    assert not any(item['class']=='lineage_mention_without_predecessor' for item in page['items'])


def test_ce21_filters_before_pagination(s14):
    service,_,_,_=s14
    _,b,_,_=ce21_setup(s14,1)
    page=s14_call(service,'query','filter-hints',collection='hints',kind='whole_snapshot_evidence',node_id='X-001',limit=1)
    assert page['total']==page['shown_count']==1
    assert page['items'][0]['target']==b

# B / CE-24–27: frozen contract of 2026-09-23; expectations are not generated.
B_BODY = ('{"audit": {"fpr": 0.010401, "n": 12, "ok": true, "tag": "v2", "nan": NaN,'
          ' "list": [1, 2], "obj": {}}, "rows": [1.5, 2.5], "a/b": 7, "m~n": 8}')
B_REF = 'S-001#results/m.json'


def bc(op='approx', path='/audit/fpr', value=0.0104, tolerance=0.00005, **fields):
    spec = dict(ref=B_REF, path=path, op=op, value=value)
    if op == 'approx':
        spec['tolerance'] = tolerance
    return {**spec, **fields}


@pytest.fixture
def b_fixture(tmp_path):
    service = NativeService(tmp_path / 'registry.sqlite3')
    root = tmp_path / 'project'
    s14_call(service, 'open', 'b-open', root=str(root), goal='B acceptance')
    s14_call(service, 'focus', 'b-focus', role='planner', mode='manual')
    (root / 'REPORT.md').write_text('# report\n')
    (root / 'results').mkdir()
    (root / 'results/m.json').write_text(B_BODY)
    pub = s14_call(service, 'publish', 'b-pub', status='partial', summary='fixture', items=[
        dict(item_id='metrics', source_path='results/m.json'),
        dict(item_id='dir', source_path='results'), dict(item_id='report', source_path='REPORT.md')])
    assert pub['publication_id'] == 'P-001'
    snap = s14_call(service, 'snapshot', 'b-snap', paths=['results/m.json'])
    assert snap['snapshot_id'] == 'S-001'
    node = s14_call(service, 'propose', 'b-node', question='root', why_now='now',
                    plan='plain plan', root_reason='independent')
    assert node['node_id'] == 'X-001' and node['question_ref'] == 'knowledge/K-001@1'
    instance = NativeStore(root)
    with instance._read() as db:
        objects = {r[0]: r[1] for r in db.execute('SELECT item_id,object_version FROM publication_items')}
    yield service, root, instance, objects
    for path in (root / '.research/objects').rglob('*'):
        if path.is_dir(): path.chmod(0o755)


def b_record(fixture, op, **fields):
    return s14_call(fixture[0], 'memory_write', op, action='record', fields={
        'kind':'claim', 'node_id':'X-001', 'statement':'plain claim', 'evidence_refs':[B_REF], **fields})


def b_counts(instance):
    with instance._read() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] if t in tables else 0
                for t in ['events','knowledge_revisions','field_checks','requests','knowledge_entries']}


CE24 = [
    ({'kind':'decision', 'checks':[bc()]}, ValidationError, 'checks: kind_not_checkable'),
    ({'checks':bc()}, ValidationError, 'checks: not_a_list'),
    ({'checks':[bc(unit='%')]}, ValidationError, 'checks[0]: unknown_field'),
    ({'checks':[bc(op='ne')]}, ValidationError, 'checks[0]: bad_op'),
    ({'checks':[bc(path='audit/fpr')]}, ValidationError, 'checks[0]: bad_pointer'),
    ({'checks':[bc(path='/a~2b')]}, ValidationError, 'checks[0]: bad_pointer'),
    ({'checks':[{k:v for k,v in bc().items() if k!='tolerance'}]}, ValidationError, 'checks[0]: tolerance_required'),
    ({'checks':[bc(tolerance=-0.00005)]}, ValidationError, 'checks[0]: negative_tolerance'),
    ({'checks':[bc(tolerance='0.1')]}, ValidationError, 'checks[0]: invalid_tolerance'),
    ({'checks':[{**bc(op='le'), 'tolerance':0.001}]}, ValidationError, 'checks[0]: tolerance_not_allowed'),
    ({'checks':[bc(op='le',value=True)]}, ValidationError, 'checks[0]: invalid_value'),
    ({'checks':[bc(value=float('nan'))]}, ValidationError, 'checks[0]: invalid_value'),
    ({'checks':[bc(op='eq',value=[1,2])]}, ValidationError, 'checks[0]: invalid_value'),
    ({'checks':[{k:v for k,v in bc(op='eq').items() if k!='value'}]}, ValidationError, 'checks[0]: invalid_value'),
    ({'checks':[bc(ref='S-001')]}, ValidationError, 'checks[0]: not_a_file'),
    ({'checks':[bc(ref='pub/P-001#dir')]}, ValidationError, 'checks[0]: not_a_file'),
    ({'checks':[bc(ref='pub/P-001#nope')]}, NotFoundError, 'checks[0]: item_missing'),
    ({'checks':[bc(ref='S-001#results/../REPORT.md')]}, ValidationError, 'checks[0]: path_escape'),
    ({'checks':[bc(ref='knowledge/K-001@1')]}, ValidationError, 'checks[0]: not_frozen'),
    ({'checks':[bc(),bc(op='ne')]}, ValidationError, 'checks[1]: bad_op'),
]


@pytest.mark.parametrize('fields,error,message', CE24, ids=range(1,21))
def test_ce24_declaration_table(b_fixture, fields, error, message):
    before = b_counts(b_fixture[2])
    with pytest.raises(error) as caught:
        b_record(b_fixture, 'ce24-bad', **fields)
    assert str(caught.value).splitlines()[0] == message
    assert b_counts(b_fixture[2]) == before


def test_ce24_observation_accepts_and_preserves_declaration(b_fixture):
    value = b_record(b_fixture, 'ce24-good', kind='observation', checks=[bc()])
    assert value['checks'] == [bc()]
    with b_fixture[2]._read() as db:
        row = db.execute('SELECT checks FROM knowledge_revisions WHERE knowledge_id=?', (value['knowledge_id'],)).fetchone()
        assert json.loads(row[0]) == [bc()]

CE25 = [
    (bc(), 'consistent', None, '0.010401'),
    (bc(tolerance=0.0000005), 'inconsistent', 'value_mismatch', '0.010401'),
    (bc('le',value=0.01), 'inconsistent', 'value_mismatch', '0.010401'),
    (bc('le',value=0.0105), 'consistent', None, '0.010401'),
    (bc('eq','/audit/n',12), 'consistent', None, '12'),
    (bc('eq','/audit/n',12.0), 'consistent', None, '12'),
    (bc('eq','/audit/n','12'), 'inconsistent', 'type_mismatch', '12'),
    (bc('eq','/audit/ok',True), 'consistent', None, 'true'),
    (bc('eq','/audit/ok',1), 'inconsistent', 'type_mismatch', 'true'),
    (bc('ge','/audit/ok',0), 'not_checkable', 'invalid_number', 'true'),
    (bc('eq','/audit/tag','v2'), 'consistent', None, '"v2"'),
    (bc('eq','/audit/tag','v3'), 'inconsistent', 'value_mismatch', '"v2"'),
    (bc('gt','/audit/tag',1), 'not_checkable', 'invalid_number', '"v2"'),
    (bc('approx','/audit/nan',0,1), 'not_checkable', 'invalid_number', 'NaN'),
    (bc('eq','/audit/nan',0), 'not_checkable', 'invalid_number', 'NaN'),
    (bc('eq','/audit/nan','NaN'), 'inconsistent', 'type_mismatch', 'NaN'),
    (bc('eq','/audit/obj','x'), 'not_checkable', 'not_a_scalar', '{}'),
    (bc('le','/rows',3), 'not_checkable', 'not_a_scalar', '[1.5, 2.5]'),
    (bc('lt','/rows/1',3), 'consistent', None, '2.5'),
    (bc('lt','/rows/2',3), 'not_checkable', 'missing_value', None),
    (bc('eq','/rows/01',2.5), 'not_checkable', 'missing_value', None),
    (bc('eq','/a~1b',7), 'consistent', None, '7'),
    (bc('eq','/m~0n',8), 'consistent', None, '8'),
    (bc('eq','/audit/missing',1), 'not_checkable', 'missing_value', None),
    (bc('eq','',1), 'not_checkable', 'not_a_scalar',
     json.dumps(json.loads(B_BODY),sort_keys=True,ensure_ascii=False,allow_nan=True)[:200]),
]


def b_results(instance, ref):
    with instance._read() as db:
        values = [dict(row) for row in db.execute('SELECT * FROM field_checks WHERE target_ref=? ORDER BY check_index', (ref,))]
    for value in values:
        value['spec'] = json.loads(value['spec'])
        value['target'] = value.pop('target_ref')
        value['sequence'] = value.pop('source_sequence')
    return values


@pytest.mark.parametrize('index', range(25), ids=range(1,26))
def test_ce25_evaluation_table(b_fixture, index, monkeypatch):
    from auto_research import frozen_refs
    opened = []
    original = frozen_refs.open
    def opening(db, root, ref):
        opened.append(ref)
        return original(db, root, ref)
    monkeypatch.setattr(frozen_refs, 'open', opening)
    value = b_record(b_fixture, 'ce25', checks=[row[0] for row in CE25])
    assert value['ref'] == 'knowledge/K-002@1'
    rows = b_results(b_fixture[2], value['ref'])
    assert len(rows) == 25
    spec,result,reason,text = CE25[index]
    assert rows[index] == dict(target=value['ref'],check_index=index,spec=spec,input_ref=B_REF,
        object_version=b_fixture[3]['metrics'],input_sha256=b_fixture[3]['metrics'],
        checker_version='field-check/1',tolerance_rule='abs' if spec['op']=='approx' else None,
        sequence=value['source_sequence'],created_at=value['created_at'],
        result=result,reason=reason,observed_text=text)
    assert opened == [B_REF]
    # Independent reference only checks the frozen oracle, not expected values from production.
    import runpy
    reference = runpy.run_path(str(Path(__file__).parent/'field_check_reference.py'))
    assert reference['evaluate'](B_BODY.encode(),spec) == dict(result=result,reason=reason,observed_text=text)
    json.dumps(rows,allow_nan=False)


@pytest.mark.parametrize('index',range(4))
def test_ce25_reference_forms_and_non_json(b_fixture,index,monkeypatch):
    from auto_research import frozen_refs
    original=frozen_refs.open
    calls=[]
    def opening(db,root,ref):
        calls.append(ref)
        return original(db,root,ref)
    monkeypatch.setattr(frozen_refs,'open',opening)
    b_record(b_fixture,'ce25-first')
    refs=['pub/P-001#metrics','pub/P-001#dir/m.json',B_REF,'pub/P-001#report']
    specs=[bc(ref=ref) for ref in refs[:3]]+[bc('eq','/x',1,ref=refs[3])]
    value=b_record(b_fixture,'ce25-forms',checks=specs)
    assert value['ref']=='knowledge/K-003@1'
    rows=b_results(b_fixture[2],value['ref'])
    assert len(rows)==4
    expected=rows[index]
    assert expected==dict(target=value['ref'],check_index=index,spec=specs[index],input_ref=refs[index],
        object_version=b_fixture[3][['metrics','dir','metrics','report'][index]],
        input_sha256=b_fixture[3]['report' if index==3 else 'metrics'],
        observed_text=None if index==3 else '0.010401',tolerance_rule=None if index==3 else 'abs',
        checker_version='field-check/1',result='not_checkable' if index==3 else 'consistent',
        reason='unsupported_format' if index==3 else None,sequence=value['source_sequence'],created_at=value['created_at'])
    assert calls==['pub/P-001#metrics','pub/P-001#dir/m.json','pub/P-001#report']


def b_revise(fixture, ref, changes, operation, *, retract=False):
    return s14_call(fixture[0], 'memory_write', operation, action='revise', fields={
        'ref':ref,'changes':changes,'reason':'revision',
        'affected_scope_mode':'versions' if retract else 'none',
        'affected_scope':[ref] if retract else [],'change_kind':'retract' if retract else 'reword'})


@pytest.mark.parametrize('stop',range(1,10),ids=range(1,10))
def test_ce26_nine_steps(b_fixture,stop):
    service,root,instance,objects=b_fixture
    c0,c1,c2=bc(),bc('le',value=0.01),bc('le',value=0.0105)
    history={}
    declarations={}
    statuses={}
    def remember(value,specs,expected):
        ref=value['ref']
        assert value['checks']==specs
        assert value['status']==('retracted' if ref=='knowledge/K-002@5' else 'proposed')
        with instance._read() as db:
            raw=db.execute('SELECT checks FROM knowledge_revisions WHERE knowledge_id=? AND revision=?',
                           (value['knowledge_id'],value['revision'])).fetchone()[0]
        assert raw==json.dumps(specs,sort_keys=True,ensure_ascii=False,allow_nan=False)
        rows=b_results(instance,ref)
        assert [(r['result'],r['reason']) for r in rows]==expected
        assert [r['spec'] for r in rows]==specs
        history[ref]=rows
        declarations[ref]=specs
        statuses[ref]=value['status']
        for prior,saved in history.items():
            assert b_results(instance,prior)==saved
            reread=s14_call(service,'query','read-'+prior,ref=prior)['value']
            assert reread['checks']==declarations[prior] and reread['status']==statuses[prior]
            # There are no knowledge dependencies in this fixture. Only the explicit
            # withdrawal of @4 can create a risk; field results must not create one.
            risk=instance.knowledge_risk([prior])['versions'][prior]
            if prior!='knowledge/K-002@4' or value['ref']!='knowledge/K-002@5':
                if prior!='knowledge/K-002@4' or 'knowledge/K-002@5' not in history:
                    assert risk['needs_action'] is False and risk['residual_use_risk']==[]
        assert s14_call(service,'query','hints-'+ref,collection='hints')['items']==[]
    for step in range(1,stop+1):
        if step==1:
            value=b_record(b_fixture,'ce26-record',checks=[c0,c1])
            assert value['ref']=='knowledge/K-002@1'
            remember(value,[c0,c1],[('consistent',None),('inconsistent','value_mismatch')])
        elif step==2:
            value=b_revise(b_fixture,value['ref'],{'statement':'reworded'},'ce26-2')
            assert value['ref']=='knowledge/K-002@2'
            remember(value,[c0,c1],[('consistent',None),('inconsistent','value_mismatch')])
        elif step==3:
            value=b_revise(b_fixture,value['ref'],{'checks':[]},'ce26-3')
            assert value['ref']=='knowledge/K-002@3'
            remember(value,[],[])
        elif step==4:
            value=b_revise(b_fixture,value['ref'],{'checks':[c2]},'ce26-4')
            assert value['ref']=='knowledge/K-002@4'
            remember(value,[c2],[('consistent',None)])
        elif step==5:
            archive=root/'.research/objects'/objects['metrics']
            archive.chmod(0o644)
            archive.write_bytes(b'changed bytes')
            damaged=b_record(b_fixture,'ce26-damaged',checks=[c0],evidence_refs=['pub/P-001#report'])
            assert damaged['ref']=='knowledge/K-003@1'
            remember(damaged,[c0],[('not_checkable','object_corrupted')])
            row=history[damaged['ref']][0]
            assert row['observed_text'] is None and row['input_sha256'] is None
            no_checks=b_record(b_fixture,'ce26-no-checks')
            assert no_checks['ref']=='knowledge/K-004@1'
            remember(no_checks,[],[])
        elif step==6:
            archive.unlink()
            before=b_counts(instance)
            with pytest.raises(ValidationError) as error:
                b_record(b_fixture,'ce26-deleted',checks=[c0],evidence_refs=['pub/P-001#report'])
            assert str(error.value).splitlines()[0]=='checks[0]: object_missing'
            assert b_counts(instance)==before
            assert b_results(instance,'knowledge/K-002@1')==history['knowledge/K-002@1']
        elif step==7:
            value=b_revise(b_fixture,value['ref'],{'status':'retracted'},'ce26-7',retract=True)
            assert value['ref']=='knowledge/K-002@5' and value['status']=='retracted'
            remember(value,[c2],[('not_checkable','object_missing')])
        elif step==8:
            no_checks=b_revise(b_fixture,no_checks['ref'],{'statement':'reworded'},'ce26-8')
            assert no_checks['ref']=='knowledge/K-004@2'
            remember(no_checks,[],[])
        else:
            before=b_counts(instance)
            with pytest.raises(NotFoundError) as error:
                b_revise(b_fixture,no_checks['ref'],{'evidence_refs':[B_REF,'pub/P-001#nope']},'ce26-9')
            assert str(error.value).splitlines()[0]=='pub/P-001#nope: item_missing'
            assert b_counts(instance)==before
    for ref,rows in history.items():
        assert b_results(instance,ref)==rows


@pytest.mark.parametrize('field',['evidence_refs','dependencies','motivated_by'])
def test_ce26_a2n6_retains_existing_references_explicitly(b_fixture,field):
    value=b_record(b_fixture,'n6-old',**{field:[B_REF]})
    archive=b_fixture[1]/'.research/objects'/b_fixture[3]['metrics']
    archive.chmod(0o644);archive.unlink()
    revised=b_revise(b_fixture,value['ref'],{field:[B_REF]},'n6-retain')
    assert revised[field]==[B_REF]
    before=b_counts(b_fixture[2])
    with pytest.raises(NotFoundError) as error:
        b_revise(b_fixture,revised['ref'],{field:[B_REF,'pub/P-001#nope']},'n6-new')
    assert str(error.value).splitlines()[0]=='pub/P-001#nope: item_missing'
    assert b_counts(b_fixture[2])==before


@pytest.mark.parametrize('entry',['reference','search','history','state_export'])
def test_ce27_query_returns_stored_results(b_fixture,entry,monkeypatch):
    from auto_research import field_checks
    service,root,instance,objects=b_fixture
    specs=[bc(),bc('le',value=0.01),bc('eq','/missing',1)]
    value=b_record(b_fixture,'ce27-record',checks=specs)
    expected=b_results(instance,value['ref'])
    archive=root/'.research/objects'/objects['metrics']
    archive.chmod(0o644);archive.unlink()
    monkeypatch.setattr(field_checks,'evaluate',lambda *a,**kw:pytest.fail('read recomputed field results'))
    before=b_counts(instance)
    if entry=='reference':
        saved=s14_call(service,'query','ce27-read',ref=value['ref'])['value']
    elif entry=='search':
        saved=s14_call(service,'query','ce27-search',kind='claim')['items'][0]
    elif entry=='history':
        saved=next(r for r in s14_call(service,'history_page','ce27-history',collection='knowledge')['items'] if r['ref']==value['ref'])
    else:
        saved=next(r for r in instance.query()['knowledge'] if r['ref']==value['ref'])
    assert saved['checks']==specs
    assert saved['field_checks']==expected
    assert [row['check_index'] for row in saved['field_checks']]==[0,1,2]
    assert b_counts(instance)==before
    context=s14_call(service,'memory_context','ce27-context')['text']
    assert 'field_checks' not in context and '"checks"' not in context


@pytest.mark.parametrize('case',['backup','rollback','columns_history','old_package','idempotent'])
def test_ce27_schema9(b_fixture,monkeypatch,case):
    from auto_research import schema9, native_store
    instance=b_fixture[2]
    value=b_record(b_fixture,'schema9-historical')
    with sqlite3.connect(instance.db_path,isolation_level=None) as db:
        db.execute('DROP TABLE field_checks')
        db.execute('ALTER TABLE knowledge_revisions DROP COLUMN checks')
        db.execute('PRAGMA user_version=8')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before=instance.db_path.read_bytes()
    if case=='rollback':
        original=schema9.ensure_schema9
        def broken(db):
            original(db)
            raise RuntimeError('injected schema9 failure')
        monkeypatch.setattr(schema9,'ensure_schema9',broken)
        with pytest.raises(RuntimeError,match='^injected schema9 failure$'):
            schema9.migrate_schema9(instance.db_path)
        with sqlite3.connect(instance.db_path) as db:
            assert db.execute('PRAGMA user_version').fetchone()[0]==8
            assert 'checks' not in {r[1] for r in db.execute('PRAGMA table_info(knowledge_revisions)')}
            assert db.execute("SELECT name FROM sqlite_master WHERE name='field_checks'").fetchall()==[]
        return
    schema9.migrate_schema9(instance.db_path)
    with sqlite3.connect(instance.db_path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==9
    if case=='backup':
        backup=instance.db_path.parent/'schema-8-backup.sqlite3'
        assert backup.read_bytes()==before
        backup.write_bytes(b'pre-existing backup')
        with sqlite3.connect(instance.db_path) as db:
            db.execute('PRAGMA user_version=8')
        schema9.migrate_schema9(instance.db_path)
        assert backup.read_bytes()==b'pre-existing backup'
    elif case=='columns_history':
        with sqlite3.connect(instance.db_path) as db:
            columns={r[1]:r for r in db.execute('PRAGMA table_info(knowledge_revisions)')}
            assert columns['checks'][2]=='TEXT' and columns['checks'][3]==1 and columns['checks'][4]=="'[]'"
            assert [r[0] for r in db.execute('SELECT checks FROM knowledge_revisions')]==['[]','[]']
            assert db.execute('SELECT COUNT(*) FROM field_checks').fetchone()[0]==0
            assert {r[1] for r in db.execute('PRAGMA table_info(field_checks)')}=={
                'target_ref','check_index','spec','input_ref','object_version','input_sha256',
                'observed_text','tolerance_rule','checker_version','result','reason','created_at','source_sequence'}
    elif case=='old_package':
        monkeypatch.setattr(native_store,'SCHEMA_VERSION',8)
        with pytest.raises(ValidationError) as error:
            NativeStore(b_fixture[1])
        assert str(error.value)=='Schema 9 must be migrated before native plugin writes'
    else:
        with sqlite3.connect(instance.db_path) as db: db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        before=instance.db_path.read_bytes()
        schema9.migrate_schema9(instance.db_path)
        assert instance.db_path.read_bytes()==before


def test_ce26_results_do_not_drive_research_state(b_fixture):
    service,_,instance,_=b_fixture
    value=b_record(b_fixture,'invariant-record',checks=[bc(),bc('le',value=0.01),bc('eq','/missing',1)])
    user=record(instance,'invariant-dependent',dependencies=[value['ref']])
    b_revise(b_fixture,value['ref'],{'status':'retracted'},'invariant-retract',retract=True)
    refs=[value['ref'],user]
    def state():
        return dict(risk=instance.knowledge_risk(refs),
                    hints=s14_call(service,'query','invariant-hints',collection='hints'),
                    status=[instance.reference_query(ref)['value']['status'] for ref in refs])
    before=state()
    assert before['status']==['proposed','proposed']
    assert before['risk']['versions'][user]['needs_action'] is True
    with sqlite3.connect(instance.db_path) as db:
        db.execute("UPDATE field_checks SET result='consistent',reason=NULL")
    assert state()==before
    with sqlite3.connect(instance.db_path) as db:
        db.execute('DELETE FROM field_checks')
    assert state()==before
