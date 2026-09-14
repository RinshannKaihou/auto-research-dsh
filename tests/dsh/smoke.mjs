#!/usr/bin/env node
/** Default: no model calls. --live: two sessions; --live-worker: just one worker. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';
import { normalizeRequest, allowedPath, makeShell, createRuntime } from '../../apps/dsh/worker.mjs';

const base = fs.mkdtempSync('/tmp/ari-dsh-m0-');
const history = path.join(base, 'views'), outside = path.join(base, 'private');
fs.mkdirSync(history); fs.mkdirSync(outside);
fs.writeFileSync(path.join(history, 'published.txt'), 'published-v1');
fs.writeFileSync(path.join(outside, 'graph.db'), 'private-core-state');
const workspace = path.join(base, 'worker'); fs.mkdirSync(workspace);
fs.mkdirSync(path.join(workspace, 'inputs'));
fs.writeFileSync(path.join(workspace, 'inputs', 'fixed.txt'), 'fixed-v1');
const request = normalizeRequest({role:'worker', prompt:'unused', workspace, read_roots:[workspace,history],
  write_roots:[workspace], readonly_roots:[path.join(workspace,'inputs')], history_dir:history,
  timeout_s:30, output_schema:{type:'object'}});
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
const report = { directory:base, live:false, checks:[] };
const check = (name, ok) => { assert.ok(ok, name); report.checks.push(name); };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

if (!process.argv.includes('--live') && !process.argv.includes('--live-worker')) {
  process.chdir(workspace);
  const runtime = await createRuntime(request, {event:()=>{}});
  const call = (name,args) => runtime.tools.execute({callId:Math.random().toString(),name,arguments:args,agent:runtime.agent,signal:new AbortController().signal});
  try {
    check('DSH SDK bootstrap inherits configured model', Boolean(runtime.selection.provider && runtime.selection.model));
    check('file tool allows workspace write', !(await call('write',{path:'allowed.txt',content:'ok'})).isError);
    check('file tool rejects history write', (await call('write',{path:path.join(history,'published.txt'),content:'bad'})).isError);
    check('file tool rejects private core read', (await call('read',{path:path.join(outside,'graph.db')})).isError);
    fs.symlinkSync(path.join(outside,'new.txt'),path.join(workspace,'dangling'));
    assert.throws(()=>allowedPath(request,'dangling',true)); report.checks.push('dangling symlink escape rejected');
    fs.symlinkSync(history,path.join(workspace,'history-link'));
    check('file tool rejects symlink history write',(await call('write',{path:'history-link/published.txt',content:'bad'})).isError);
    check('structured submit validates required schema shape',(await call('submit_result',[])).isError);
    const shell = runtime.shell;
    const result = await shell.execute(`pwd; printf ok > shell-ok; cat ${quote(history+'/published.txt')}; printf bad > ${quote(history+'/published.txt')}; printf bad > inputs/fixed.txt; cat ${quote(outside+'/graph.db')}; printf bad > ${quote(outside+'/new.txt')}`);
    check('kernel permits correct cwd and workspace write', result.stdout.includes(request.workspace) && fs.readFileSync(workspace+'/shell-ok','utf8')==='ok');
    check('kernel permits published history read', result.stdout.includes('published-v1'));
    check('kernel rejects history/input/private-state writes and private reads', /Operation not permitted/.test(result.stderr) && !result.stdout.includes('private-core-state') && !fs.existsSync(outside+'/new.txt') && fs.readFileSync(history+'/published.txt','utf8')==='published-v1' && fs.readFileSync(workspace+'/inputs/fixed.txt','utf8')==='fixed-v1');
    await shell.execute('ln inputs/fixed.txt aliased-input; printf changed > aliased-input; mv inputs hidden-inputs; printf changed > hidden-inputs/fixed.txt');
    check('kernel rejects readonly hardlink and rename bypass',fs.readFileSync(workspace+'/inputs/fixed.txt','utf8')==='fixed-v1' && !fs.existsSync(workspace+'/hidden-inputs'));
    const abort = new AbortController();
    const task = shell.execute("sleep 20 & child=$!; printf '%s' $child > child.pid; wait $child; printf leaked > leaked.txt",abort.signal);
    for(let i=0;i<100 && !fs.existsSync(workspace+'/child.pid');i++) await delay(20);
    check('foreground shell child actually starts',fs.existsSync(workspace+'/child.pid'));
    const pid=Number(fs.readFileSync(workspace+'/child.pid','utf8'));
    abort.abort(); const stopped=await task;
    let alive=true; try{process.kill(pid,0);}catch{alive=false;}
    check('cancel reaps tool child before completion',stopped.cancelled && !alive && !fs.existsSync(workspace+'/leaked.txt'));
  } finally {await runtime.close();}
} else {
  report.live=true;
  const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
  const schemas=JSON.parse(execFileSync('python3',['-c','import json; from auto_research.prompts import WORKER_SCHEMA,COORDINATOR_SCHEMA; print(json.dumps({"worker":WORKER_SCHEMA,"coordinator":COORDINATOR_SCHEMA}))'],{encoding:'utf8',env:{...process.env,PYTHONPATH:path.join(repo,'src')}}));
  const workerPrompt='Small backend smoke, not research. First write progress.json with {"state":"draft saved","next":"verify later"}, then write draft.txt with "Incomplete derivation: assumption A remains unproved." Use the write tool. Return close_reason="stage complete", limitations="Smoke only; A unproved", products=[{"id":"draft","path":"draft.txt","interface":"plain text unfinished derivation","status":"partial","gaps":["A unproved"]}], findings=[], inputs=[], next=["Verify A"].';
  const coordPrompt='Small backend smoke. Return one proposal: question="Q-001", why_now="An unfinished derivation has assumption A", plan="Inspect and attempt to justify A in a bounded next stage", modifies_artifact=false, inputs=[]. notes="Plan only; no established finding" and pause_reason="". Do not use tools.';
  async function run(role,prompt) {
    const work=path.join(base,role+'-live');fs.mkdirSync(work);
    const input=path.join(base,role+'-request.json'), output=path.join(base,role+'-output.json');
    fs.writeFileSync(input,JSON.stringify({...request,role,prompt,workspace:work,read_roots:[work,history],write_roots:[work],readonly_roots:[],timeout_s:180,output_schema:schemas[role]}));
    const log=fs.openSync(path.join(base,role+'.log'),'w');
    const child=spawn(process.execPath,[path.join(repo,'apps/dsh/worker.mjs'),input,output],{cwd:work,stdio:['ignore',log,log],env:{...process.env,ARI_DSH_MAX_TOKENS:'2048'}});
    const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',resolve);});fs.closeSync(log);
    check(role+' headless process completes',code===0);
    const envelope=JSON.parse(fs.readFileSync(output,'utf8'));
    check(role+' has session id and numeric token usage',typeof envelope.session_id==='string' && typeof envelope.usage.tokens==='number' && envelope.usage.tokens>0);
    const events=fs.readFileSync(path.join(base,role+'.log'),'utf8').trim().split('\n').map(line=>{try{return JSON.parse(line);}catch{return {};}});
    check(role+' actual process cwd is private workspace',events.some(event=>event.type==='ready' && event.cwd===fs.realpathSync(work)));
    if(role==='worker') check('worker preserves progress and incomplete product',fs.existsSync(work+'/progress.json') && fs.readFileSync(work+'/draft.txt','utf8').includes('unproved') && envelope.result.products[0].status==='partial');
    else check('coordinator returns validated proposal schema',envelope.result.proposals.length===1 && envelope.result.proposals[0].question==='Q-001');
    return {role,session_id:envelope.session_id,tokens:envelope.usage.tokens};
  }
  // Both are independent process cwd values; no process.chdir is shared across agents.
  report.sessions=await Promise.all(process.argv.includes('--live-worker') ? [run('worker',workerPrompt)] : [run('worker',workerPrompt),run('coordinator',coordPrompt)]);
}
fs.writeFileSync(path.join(base,'report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
