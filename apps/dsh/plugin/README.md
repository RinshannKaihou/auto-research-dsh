# auto-research-v5 for DSH

This npm package is a native DSH research workflow plugin. It registers one
`/research` command group, eight model tools, a Research conversation view,
research context injection, host event projection, model usage observation,
and native goal control. DSH remains the sole owner of model calls, native
tools, permissions, sessions, transcripts, cancellation, and goal rounds.

## Requirements and installation

- an installed DSH profile;
- Python 3.11 or newer on `PATH` (or configured through `python`);
- no Python packages and no separately started service.

Build and install from this directory:

```bash
npm pack
dsh plugin --profile <profile-name> add ./auto-research-v5-0.3.3.tgz
```

Replace `<profile-name>` with the profile to modify, for example `web`, and
restart that profile when the DSH command asks you to do so.

The `prepack` step copies only the storage, artifact, and migration Python
modules into the tarball. The plugin starts that code as a bounded private
stdio child. It does not bundle the schema-1 worker, Runtime, scheduler, GUI,
or a fallback harness.

Optional plugin configuration:

```yaml
- id: auto-research-v5
  config:
    python: /absolute/path/to/python3
    registryPath: /absolute/path/to/private/registry.sqlite3
    autonomousConcurrency: 2
    maxGoalRounds: 20
```

`pythonModulePath` is available only for source-checkout development. A packed
plugin defaults to its bundled `python/` directory. The default registry is
`~/.dsh/auto-research-v5/registry.sqlite3`.

## Native use

Run `/research init <goal>` in a normal DSH session. The
project root is the session's current `cwd`; browser and model payloads cannot
choose another root. Continue chatting normally and use DSH tools normally.

Commands:

```text
/research init <goal>
/research open
/research status
/research focus [planning|<node-id>]
/research auto
/research pause
/research resume
/research stop
/research branch <node-id>
/research restore <snapshot-id>
/research detach
```

Model tools are `research_query`, `research_propose`, `research_note`,
`research_snapshot`, `research_publish`, `research_relate`, `research_finish`,
and `research_close_node`. Tool identity comes from `exec.agent`, and native
tool call IDs are the stable business idempotency keys.

Autonomous mode creates a plugin-owned native goal. It refuses to replace a
non-plugin goal. Human input pauses that session's plugin goal and remains an
ordinary user turn. Project pause and resume affect all associated plugin goals.
Cold-restored goals stay disarmed until explicit resume.

`/research auto` also opens an autonomous planning work segment when the
session does not have one. Project control is persisted before the native goal
is armed, so the first `agent/pre-step` cannot mistake startup for a manual or
paused project and immediately pause it.

## Security and failure semantics

The base product follows DSH's normal single-user trusted-workspace permission
model. Separate branch `cwd` values provide correct execution attribution and
write destinations; they are not a claim of strict cross-session read isolation.

Publication and snapshot source paths reject `.research`, `.git`, `.env`, and
credential-like names. Immutable object bytes are fixed before the schema-2
transaction. Lost responses and IPC retries reuse their operation intent.
Usage is observation-only: recorded tokens and missing observations are shown,
but neither creates a hard or soft limit or pauses autonomous continuation.

Run the no-network deterministic profile with:

```bash
python3 tests/dsh-profile/run.py \
  --dsh-root /absolute/path/to/@deepseek-ai/dsh
```

Never load `tests/dsh-profile/probe.mjs` into a real profile. It is a fixture
provider that rejects all non-fixture provider requests.

## Research graph (0.3.3)

The Research view defaults to a deterministic SVG node graph (a list on narrow
screens). Selection is read-only; focus and branch require explicit buttons.
Edges display recorded relations, fixed inputs, and history anchors. Publications,
snapshots, and all attempts remain in the details; unassigned planning records
remain accessible below the graph. Token usage is observation-only.

See `docs/WORKBENCH.md` in the source repository for controls and failure semantics.
The client uses the host React instance and no additional graph runtime.
Edit `frontend/*.js`, then run `npm run build:client`; `npm run check:client`
verifies the committed bundle. `npm pack` rebuilds it automatically.
From the repository root, `make test-dsh-plugin` exercises projection, layout,
async UI state transitions, and plugin boundaries.
