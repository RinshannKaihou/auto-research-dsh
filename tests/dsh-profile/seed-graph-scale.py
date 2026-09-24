#!/usr/bin/env python3
"""Expand only a disposable deterministic DSH probe project to 200 nodes/500 edges."""
import argparse,json
from pathlib import Path
from auto_research.native_store import NativeStore
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--workspace',required=True,type=Path)
a=p.parse_args()
if 'ari-native-profile-' not in str(a.workspace.resolve()):
 raise SystemExit('Only ari-native-profile-* disposable paths are allowed')
s=NativeStore(a.workspace)
state=s.query()
if state['project']['goal']!='Deterministic native research':
 raise SystemExit('Refusing a non-fixture research project')
for i in range(len(state['nodes']),200):
 s.propose(f'Scale research question {i+1:03d}','Deterministic UI scale fixture','Read-only graph performance inspection',f'scale-node-{i}')
state=s.query()
pairs={(r['source_ref'],r['target_ref']) for r in state['relations']}
for offset in range(1,200):
 for i in range(200):
  pair=(f'X-{i+1:03d}',f'X-{(i+offset)%200+1:03d}')
  if len(pairs)>=500:break
  if pair not in pairs:
   s.relate(*pair,'fixture-link','Deterministic scale edge',f'scale-edge-{pair[0]}-{pair[1]}');pairs.add(pair)
 if len(pairs)>=500:break
print(json.dumps({'nodes':len(s.query()['nodes']),'directed_pairs':len(pairs)}))
