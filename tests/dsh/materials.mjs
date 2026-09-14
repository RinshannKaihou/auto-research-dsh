/** Real file/Poppler checks, no model calls. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { normalizeRequest, readMaterial } from '../../apps/dsh/worker.mjs';
const base = fs.mkdtempSync('/tmp/ari-materials-');
const workspace = path.join(base, 'workspace'), materials = path.join(base,'selected materials');
for (const dir of [workspace,materials,path.join(workspace,'.home'),path.join(workspace,'.tmp')]) fs.mkdirSync(dir);
const request = normalizeRequest({role:'coordinator',prompt:'test',workspace,read_roots:[workspace,materials],output_schema:{type:'object'}});
function pdf(text) {
  const stream = `BT /F1 12 Tf 30 80 Td (${text}) Tj ET`;
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 150] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`];
  let result = '%PDF-1.4\n'; const offsets = [0];
  objects.forEach((object,i)=>{ offsets.push(Buffer.byteLength(result)); result += `${i+1} 0 obj\n${object}\nendobj\n`; });
  const start = Buffer.byteLength(result);
  result += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
  return result;
}
const file = path.join(materials,'Gaussian paper.pdf');
fs.writeFileSync(file,pdf('Gaussian bias variance derivation'));
const original = fs.readFileSync(file);
const listing = await readMaterial(request,materials);
assert.equal(listing.format,'directory'); assert.equal(listing.entries[0].name,'Gaussian paper.pdf');
const extracted = await readMaterial(request,file,{limit:8});
assert.equal(extracted.format,'pdf-text'); assert.equal(extracted.content,'Gaussian'); assert.equal(extracted.next_offset,8);
assert.match((await readMaterial(request,file,{offset:8})).content,/bias variance derivation/);
assert.deepEqual(fs.readFileSync(file),original);
await assert.rejects(readMaterial(request,path.join(base,'private.txt')),/ENOENT|READ_DENIED/);
fs.writeFileSync(path.join(base,'private.txt'),'private');
fs.symlinkSync(path.join(base,'private.txt'),path.join(materials,'escape'));
await assert.rejects(readMaterial(request,path.join(materials,'escape')),/READ_DENIED/);
await assert.rejects(readMaterial(request,file,{offset:-1}),/INVALID_READ_RANGE/);
fs.writeFileSync(path.join(materials,'note.txt'),'中文原始材料');
assert.equal((await readMaterial(request,path.join(materials,'note.txt'))).content,'中文原始材料');
fs.writeFileSync(path.join(materials,'binary.bin'),Buffer.from([0,1,2]));
await assert.rejects(readMaterial(request,path.join(materials,'binary.bin')),/BINARY_FILE_NOT_SUPPORTED/);
console.log(JSON.stringify({passed:true,model_calls:0,checks:['directory discovery','PDF extraction under sandbox','text pagination','source unchanged','private path and symlink denied','invalid range','UTF-8 text','binary rejection'],directory:base}));
