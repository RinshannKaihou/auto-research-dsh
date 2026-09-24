# ScienceFlow reference boundary

The implementation uses
[`science-learner/ScienceFlow@c1e3c09`](https://github.com/science-learner/ScienceFlow/tree/c1e3c09ee94afcecc8f2d0ca080e761e7c0f8a5b)
as a fixed design reference. It does not use ScienceFlow as a runtime dependency.

| Reference idea | v5 adaptation | Main implementation |
|---|---|---|
| Work snapshots | Immutable selected-file snapshots, then a new DSH session in a new work copy | `NativeService._snapshot_manifest`, `prepare_restore` |
| Stage ledger | Repeated immutable publications; partial and empty-finding stages are valid | `NativeStore.publish_metadata` |
| Fold/index/unfold memory | Bounded prompt view plus full `research_query` expansion | `NativeStore.memory_view`, `NativeStore.query` |
| Recovery preflight | A takeover is refused while the source work segment is active or unknown | `NativeService.prepare_restore` |
| ESTRA | Node proposals record `continue`, `redirect`, or `anchor`; an anchor must resolve to immutable history | `NativeStore.propose`, `research_propose`, `prepare_branch` |

The adaptation keeps execution ownership in DSH. The plugin never starts its
own model loop, scheduler, worker, or fallback harness. Attribution shipped in
the npm package is recorded in `apps/dsh/plugin/LICENSES/SCIENCEFLOW.md`.

