// shuohao character-refs custom adapter: hands each render job to the agent, who renders it with
// Higgsfield GPT Image 2 and writes the PNG to --out. Blocks until the PNG exists.
import fs from 'node:fs'; import path from 'node:path';
const a = process.argv.slice(2); const o = {};
for (let i = 0; i < a.length; i += 2) o[a[i].replace(/^--/, '')] = a[i + 1];
const Q = path.join(path.dirname(new URL(import.meta.url).pathname), 'queue');
const id = Date.now() + '-' + Math.random().toString(36).slice(2, 6);
const job = { id, prompt: fs.readFileSync(o.prompt, 'utf8'), negative: o.neg ? fs.readFileSync(o.neg, 'utf8') : '',
  refs: [o.ref1, o.ref2, o.ref3].filter(Boolean), width: +o.w, height: +o.h, ratio: o.ratio, out: o.out };
fs.writeFileSync(path.join(Q, id + '.json'), JSON.stringify(job, null, 2));
const t0 = Date.now();
while (!fs.existsSync(o.out)) { if (Date.now() - t0 > 3600e3) { console.error('relay timeout'); process.exit(1); } Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2000); }
fs.renameSync(path.join(Q, id + '.json'), path.join(Q, id + '.done.json'));
