// Integration-test helper only: a fresh local D1, no Cloudflare account or network deployment.
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { toolchainPath } from '../../../scripts/cloudflare/shop_stats/local_tools.mjs';
const require = createRequire(resolve(toolchainPath(),'package.json'));
const { Miniflare,convertV4MiniflareOptions } = require('miniflare');
const source = fileURLToPath(new URL('../../../scripts/cloudflare/shop_stats/',import.meta.url));
const mf = new Miniflare(convertV4MiniflareOptions({modules:[
  {type:'ESModule',path:resolve(source,'worker.mjs')},{type:'ESModule',path:resolve(source,'views.mjs')}],
  modulesRoot:source,compatibilityDate:'2026-10-03',d1Databases:{DB:randomUUID()},
  d1Persist:resolve(process.env.SHOP_STATS_TEST_OUTPUT,'integration-d1')}));
try {
  const db = await mf.getD1Database('DB');
  const sql = await readFile(resolve(source,'migrations/0001_reports.sql'),'utf8');
  const position = sql.indexOf('CREATE TRIGGER');
  const statements = sql.slice(0,position).replace(/^--.*$/gm,'').split(';').map(s => s.trim()).filter(Boolean);
  await db.batch(statements.map(statement => db.prepare(statement)));
  await db.prepare(sql.slice(position)).run();
  process.stdout.write(JSON.stringify({url:String(await mf.ready)})+'\n');
  await new Promise(resolve => {process.stdin.once('data',resolve);process.stdin.once('end',resolve);process.stdin.resume();});
} finally { await mf.dispose(); }
