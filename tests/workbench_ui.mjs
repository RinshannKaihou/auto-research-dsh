import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = fs.readFileSync(
  path.join(root, "src/auto_research/assets/research-workbench.html"),
  "utf8",
);
const source = html.split("// WORKBENCH_LOGIC_START")[1].split("// WORKBENCH_LOGIC_END")[0];
const context = vm.createContext({ URL });
vm.runInContext(source + "\nglobalThis.logic = WorkbenchLogic;", context);
const { countActive, status, resourceValues, selectedFromHash, exportPath } = context.logic;

assert.equal(countActive({ active_attempts: 2 }), 2);
assert.equal(countActive({ active_attempts: [{ id: "A-1" }, { id: "A-2" }] }), 2);
assert.equal(countActive({}), 0);
assert.equal(status({ control: "paused", node_count: 0, active_attempts: 0 }).label, "尚未开始");
assert.equal(
  status({ control: "running", controller_running: true, active_attempts: 2 }).label,
  "研究进行中",
);
assert.equal(
  status({ control: "paused", controller_running: true, active_attempts: 2 }).label,
  "正在收尾",
);
assert.equal(status({ control: "stopped", active_attempts: 1 }).label, "正在停止");
assert.equal(status({ control: "stopped", active_attempts: 0 }).label, "已停止");
assert.equal(
  status({ control: "running", controller_running: false, active_attempts: 0 }).label,
  "等待接续",
);
assert.equal(
  status({ control: "running", controller_running: false, active_attempts: 2 }).label,
  "执行尚待核对",
);
assert.equal(status({ control: "paused", pending_action: "start" }).label, "正在开始");
assert.equal(status({ control: "running", pending_action: "stop" }).label, "正在停止");
assert.equal(status({ error: "backend unavailable" }).label, "需要关注");

assert.deepEqual(
  [...resourceValues([" /tmp/paper.pdf ", "", "/tmp/paper.pdf", "/tmp/代码"])],
  ["/tmp/paper.pdf", "/tmp/代码"],
);
assert.equal(selectedFromHash("#project=research-1"), "research-1");
assert.equal(selectedFromHash("#project=%E7%A0%94%E7%A9%B6"), "研究");
assert.equal(selectedFromHash("#project=%broken"), null);
assert.equal(selectedFromHash("#something-else"), null);
assert.equal(
  exportPath("/export?project=research-1", "http://127.0.0.1:8000"),
  "http://127.0.0.1:8000/export?project=research-1",
);
for (const value of [
  "https://example.com/export?x=1",
  "//example.com/export?x=1",
  "/other?x=1",
  "javascript:alert(1)",
  null,
]) {
  assert.equal(exportPath(value, "http://127.0.0.1:8000"), null);
}

assert.equal(html.split("__ARI_GUI_BOOT__").length - 1, 1);
assert(!/\.innerHTML\s*=/.test(html));
assert(!/\b(?:src|href)=["']https?:/i.test(html));
assert(html.includes("X-ARI-Token"));
assert(html.includes('credentials: "same-origin"') || html.includes('credentials:"same-origin"'));
const script = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
console.log(
  "Workbench state, pending actions, resources, hash selection, export safety and script checks passed.",
);
const { activityMessage } = context.logic;
const activeMessage = activityMessage({node_count:0,control:'running',active_attempts:1,activity:[{id:'A-001',role:'coordinator',state:'running',provider:'test',model:'model',elapsed_seconds:75,event:{type:'tool_finished',tool:'read'}}]});
assert.match(activeMessage, /第一批探索/);
assert.match(activeMessage, /test \/ model/);
assert.match(activeMessage, /1分15秒/);
assert.match(activeMessage, /读取材料完成/);
assert.doesNotMatch(activityMessage({node_count:0,control:'stopped',active_attempts:0,activity:[{id:'A-001',role:'coordinator',state:'interrupted'}]}), /正在规划/);
assert.match(activityMessage({activity:[{id:'A-001',event:{type:'tool_denied_or_failed',tool:'read'}}]}), /读取材料失败/);
