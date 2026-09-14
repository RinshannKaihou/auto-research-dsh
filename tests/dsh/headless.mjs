#!/usr/bin/env node
/** Parameter/config/error-envelope tests. Never sends an LLM request. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {normalizeRequest,createRuntime} from '../../apps/dsh/worker.mjs';

const base=fs.mkdtempSync('/tmp/ari-dsh-headless-');
const workspace=path.join(base,'workspace');fs.mkdirSync(workspace);
const script=fileURLToPath(new URL('../../apps/dsh/worker.mjs',import.meta.url));
const valid={role:'worker',prompt:'unused',workspace,output_schema:{type:'object'}};
const checks=[];
assert.notEqual(spawnSync(process.execPath,[script],{encoding:'utf8'}).status,0); checks.push('missing argv exits nonzero');
for(const [label,change,expected] of [
  ['role',{role:'other'},'INVALID_REQUEST'],
  ['write roots',{write_roots:[base]},'WRITE_ROOT_MUST_EQUAL_WORKSPACE'],
  ['schema',{output_schema:{type:'array'}},'OUTPUT_SCHEMA_MUST_BE_OBJECT'],
  ['timeout',{timeout_s:-1},'INVALID_TIMEOUT'],
  ['config',{dsh_config:path.join(base,'absent')},'INVALID_DSH_CONFIG'],
]) {
  const input=path.join(base,label.replaceAll(' ','-')+'.json'),output=input+'.output';
  fs.writeFileSync(input,JSON.stringify({...valid,...change}));
  const child=spawnSync(process.execPath,[script,input,output],{encoding:'utf8'});
  assert.notEqual(child.status,0);const value=JSON.parse(fs.readFileSync(output,'utf8'));
  assert.equal(value.error.code,expected);assert.equal(value.result,null);assert.equal(value.usage.tokens,null);checks.push(label+' rejects and persists failure envelope');
}
const custom=path.join(base,'settings.json');
fs.writeFileSync(custom,JSON.stringify({'agent-default-model':{provider:'fixture-provider',model:'fixture-model',reasoningEffort:'low'}}));
const before=fs.readFileSync(custom,'utf8');
process.chdir(workspace);
const runtime=await createRuntime(normalizeRequest({...valid,dsh_config:custom}),{event:()=>{}});
try {
  assert.deepEqual(runtime.selection,{provider:'fixture-provider',model:'fixture-model',reasoningEffort:'low'});
  assert.equal(fs.readFileSync(custom,'utf8'),before);checks.push('explicit settings file selection is honored without mutation');
}finally{await runtime.close();}
const home=path.join(base,'custom-home');fs.mkdirSync(home);fs.writeFileSync(path.join(home,'settings.yaml'),'{}\n');
const input=path.join(base,'missing-model.json'),output=input+'.output';
fs.writeFileSync(input,JSON.stringify({...valid,dsh_config:home}));
const child=spawnSync(process.execPath,[script,input,output],{encoding:'utf8'});
assert.notEqual(child.status,0);assert.equal(JSON.parse(fs.readFileSync(output)).error.code,'NO_CONFIGURED_DSH_MODEL');
checks.push('missing model fails without fallback or LLM call');
const report={directory:base,model_calls:0,checks};fs.writeFileSync(path.join(base,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));
