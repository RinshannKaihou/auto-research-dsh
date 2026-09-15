# auto-research-v5 0.4.0

Native DSH research plugin. DSH owns models, tools, permissions, sessions, transcripts and goal continuation. The bundled Python 3.11+ standard-library process only stores research state; no Python packages, external service, worker or fallback harness are required.

## Install

Build with `npm pack` in this directory. Install in your chosen profile:

```bash
dsh plugin --profile web add ./auto-research-v5-0.4.0.tgz --offline
```

Restart DSH yourself after installation. Installation does not restart DSH or resume research. The package includes its Python source. Configure `python` only if Python 3.11+ is not on PATH. Missing Python affects the plugin, not normal DSH chat. No network installation script runs on plugin load.

Existing schema-2 projects upgrade transactionally to schema 3 on open, after a consistent `schema-2-backup.sqlite3` backup. Preserve the whole old project before first use; rehearse using the copy-only CLI below. Never point 0.3.x at an upgraded schema-3 database. Cold goals remain disarmed pending explicit resume.

## Use

In an ordinary DSH session whose cwd is your research project:

```text
/research init <research objective>
/research auto
/research status
/research pause
/research resume
/research stop
/research discuss <node-id>
/research restore <snapshot-id>
/research restore <snapshot-id> --preview-id <preview-id>
/research detach
```

Initialization associates the native session as the project's main session without calling a model. `/research open` associates the current cwd's existing project. Continue manual chat normally, or explicitly start autonomous research. The Research tab opens the graph, project controls, discussions, materials, snapshots and native session navigation.

Clicking a node is read-only. User `focus` and `branch` are retired. **Discuss this node** creates/reuses a separate manual session with frozen background and material versions; it neither starts a goal nor writes research records. Research write tools are rejected on the server. Discussion tokens count toward the project and are also shown separately.

Project controls target the main and exploration sessions, including when invoked from a discussion. Pause permits the current turn to finish. Resume preserves human, native Stop, failure and completed-session pauses. Stop records intent, requests owned cancellation, and remains unverified until turns/jobs exit. Repeated start does not duplicate work; after verified stop, start again in the original main session. A user's native goal is never overwritten. Goal completion does not publish or close a research node.

## Agent tools and concurrency

`research_query`, `research_propose`, `research_note`, `research_snapshot`, `research_publish`, `research_relate`, `research_finish`, `research_close_node`, `research_dispatch`, `research_wait`.

Agents dispatch node-specific tasks using durable intents and stable session IDs. `research_propose(dispatch=true)` uses the same dispatch path. Each task receives its question, plan and immutable inputs in its own cwd. The default two execution slots include the main session; `research_wait` pauses native continuation and releases the slot after its turn and jobs finish. Publication, completion and failure notifications use the native plugin inbox with persistent deduplication. Only waiting sessions can be automatically resumed by a result.

Partial publications, zero experiments and open nodes are valid. Immutable references use `pub/<publication_id>#<item_id>`. Source files are fixed before publication commit, so retries return the original bytes and result. Historical file snapshots contain source metadata and handoff context. Restore preview does not create files, sessions or model calls; execution creates a new manual handoff session and never clears the old cwd.

## Usage and configuration

Usage is monitoring only, with actual, estimated, in-progress, missing and discussion values. There is no token/cost ceiling. Missing usage or normal IPC backlog does not pause research. Confirmed state-storage failure pauses plugin-owned autonomous goals and reports the reason. Late usage remains attributed to its original turn and attempt. Native auxiliary calls are included when the host provides session identity; absent identity is not guessed.

Optional plugin config: `python`, `registryPath`, `autonomousConcurrency` (default 2), `maxGoalRounds` (optional native round limit). `pythonModulePath` is a development override. Native round limits are host controls, not usage limits. The default registry is `~/.dsh/auto-research-v5/registry.sqlite3`.

Independent cwd values express execution ownership, not strong cross-directory read isolation. DSH permissions still apply. Snapshots exclude runtime/Git directories and credential-like paths. Recovery does not replay unresolved commands.

## Offline maintenance (source checkout)

```bash
PYTHONPATH=src python3 -m auto_research.maintenance_cli -p /old/project migration migrate-copy --destination /new/project-copy
PYTHONPATH=src python3 -m auto_research.maintenance_cli -p /new/project-copy validate
PYTHONPATH=src python3 -m auto_research.maintenance_cli -p /new/project-copy export --output /new/state-export.json
```

For rollback, first export schema-3 state and preserve its entire project directory (including objects, snapshots, usage and native session references). Then select a separate compatible pre-upgrade copy and reinstall the old package. Never downgrade the only database or delete post-upgrade materials. No reverse schema writer is supplied.
