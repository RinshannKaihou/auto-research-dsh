# ScienceFlow reference notice

This plugin was designed with reference to ScienceFlow at commit
[`c1e3c09ee94afcecc8f2d0ca080e761e7c0f8a5b`](https://github.com/science-learner/ScienceFlow/tree/c1e3c09ee94afcecc8f2d0ca080e761e7c0f8a5b).

The design adapts the following ideas to DSH's native session and goal model:

- immutable work snapshots and resumable work copies;
- separate stage/publication records and fixed inputs;
- bounded memory views backed by expandable full records;
- recovery preflight before execution resumes;
- ESTRA-style `continue`, `redirect`, and historical `anchor` branch choices.

No ScienceFlow runtime is bundled or imported, and no verbatim ScienceFlow
source code is included. ScienceFlow is distributed under the MIT License; see
the fixed upstream revision for its copyright and complete license text.

