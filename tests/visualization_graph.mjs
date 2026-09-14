import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = fs.readFileSync(path.join(root, "src/auto_research/assets/research-map.html"), "utf8");
const source = html.split("// GRAPH_LOGIC_START")[1].split("// GRAPH_LOGIC_END")[0];
const context = vm.createContext({});
vm.runInContext(source + "\nglobalThis.logic = GraphLogic;", context);
const { layout, graph, reach, aggregateEdges, nodeFromRef, matches } = context.logic;
const nodes = (...ids) => ids.map((id) => ({ id }));
const edge = (source, target, kind = "input") => ({ source, target, kind });
const serialized = (positions) => JSON.stringify([...positions.entries()]);

assert.equal(layout([], []).positions.size, 0);
const treeNodes = nodes("A", "B", "C", "D", "E");
const treeEdges = [edge("A", "B"), edge("A", "C"), edge("B", "D"), edge("C", "E"), edge("D", "E")];
const tree = layout(treeNodes, treeEdges);
assert.equal(tree.positions.size, 5);
for (const link of treeEdges) {
  assert(tree.positions.get(link.source).x < tree.positions.get(link.target).x);
}
assert.notEqual(tree.positions.get("B").y, tree.positions.get("C").y);
assert.equal(
  serialized(tree.positions),
  serialized(layout([...treeNodes].reverse(), [...treeEdges].reverse()).positions),
);

const cycle = layout(nodes("A", "B", "C", "D", "R"), [
  edge("A", "B"),
  edge("B", "C"),
  edge("C", "A", "revision"),
  edge("C", "D"),
  edge("R", "R"),
]);
assert.equal(cycle.positions.size, 5);
assert.equal(cycle.positions.get("A").x, cycle.positions.get("C").x);
assert(cycle.positions.get("D").x > cycle.positions.get("C").x);
assert.equal(new Set([...cycle.positions.values()].map((p) => p.x + ":" + p.y)).size, 5);
for (const position of cycle.positions.values()) {
  assert(Object.values(position).every(Number.isFinite));
}

const disconnected = layout(nodes("A", "B", "C", "D"), [
  edge("A", "B"),
  edge("C", "D"),
  edge("missing", "A"),
]);
assert.equal(disconnected.positions.size, 4);
const adjacency = graph(nodes("A", "B", "C", "D"), [
  edge("A", "B"),
  edge("B", "C"),
  edge("C", "A"),
  edge("C", "D"),
]);
assert.deepEqual([...reach("A", adjacency.outgoing)].sort(), ["B", "C", "D"]);
assert.deepEqual([...reach("A", adjacency.incoming)].sort(), ["B", "C"]);
assert.equal(reach("missing", adjacency.incoming).size, 0);

const combined = aggregateEdges([
  { ...edge("A", "B"), ref: "A/result#proof", use: "继续推导" },
  { ...edge("A", "B"), ref: "A/result#data", use: "对照" },
  { ...edge("A", "B", "revision"), ref: "A/result#finding", finding_ref: "B/result#correction" },
]);
assert.equal(combined.length, 2);
assert.equal(combined.find((e) => e.kind === "input").references.length, 2);
assert.equal(
  combined.find((e) => e.kind === "revision").references[0].finding_ref,
  "B/result#correction",
);
assert.notEqual(combined[0].lane, combined[1].lane);

const known = new Set(["X-001", "X-002"]);
assert.equal(nodeFromRef("X-001/result#proof", known), "X-001");
assert.equal(nodeFromRef("X-999/result#proof", known), null);
assert.equal(nodeFromRef("https://example.com/result#proof", known), null);
assert.equal(nodeFromRef("javascript:alert(1)", known), null);
assert.equal(nodeFromRef(null, known), null);
const sample = {
  id: "X-001",
  question: "Q-old",
  status: "open",
  why_now: "检查推导的边界条件",
  result: { products: [{ id: "Proof" }] },
};
assert(matches(sample, "边界", "Q-old", "open", new Set()));
assert(matches(sample, "proof", "", "", new Set()));
assert(!matches(sample, "不存在的内容", "", "", new Set()));
assert(!matches(sample, "", "Q-new", "", new Set()));
assert(!matches(sample, "", "", "closed", new Set()));
assert(matches(sample, "", "", "attention", new Set(["X-001"])));
assert(!matches(sample, "", "", "attention", new Set()));

// A large cycle exercises the iterative traversal rather than the JS call stack.
const many = Array.from({ length: 2500 }, (_, i) => ({ id: `X-${i}` }));
const loop = many.map((node, i) => edge(node.id, many[(i + 1) % many.length].id));
const large = layout(many, loop);
assert.equal(large.positions.size, many.length);
assert.equal(large.groups.length, 1);

assert.equal(html.split("__ARI_GRAPH_DATA__").length - 1, 1);
assert.equal(html.split("__ARI_LIVE__").length - 1, 1);
assert(!/\.innerHTML\s*=/.test(html));
assert(!/\b(?:src|href)=["']https?:/i.test(html));
const executable = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];
new vm.Script(executable.replace("__ARI_LIVE__", "false"));
console.log(
  "Graph layout, cycles, references, filters, script syntax and offline resource checks passed.",
);
