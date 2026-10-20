import assert from 'node:assert/strict';
import { test, before, beforeEach, after } from 'node:test';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import vm from 'node:vm';
import worker from '../../../scripts/cloudflare/shop_stats/worker.mjs';
import { clientScript } from '../../../scripts/cloudflare/shop_stats/views.mjs';
import { toolchainPath } from '../../../scripts/cloudflare/shop_stats/local_tools.mjs';

// Installed tools are shared; test database/output remains task-owned.
const require = createRequire(resolve(toolchainPath(),'package.json'));
const { Miniflare, convertV4MiniflareOptions } = require('miniflare');
const source = fileURLToPath(new URL('../../../scripts/cloudflare/shop_stats/',import.meta.url));
let mf, db;
const payload = (values = {}) => ({ run_id: randomUUID().replaceAll('-',''), strategy:'transfer',
  skystone_spent:3,covenant_purchases:1,mystic_purchases:0,...values });
const send = data => mf.dispatchFetch('http://local/v1/shop-runs',{
  method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data) });
const stats = async () => (await mf.dispatchFetch('http://local/v1/shop-stats')).json();

before(async () => {
  mf = new Miniflare(convertV4MiniflareOptions({ modules:[
    {type:'ESModule',path:resolve(source,'worker.mjs')},
    {type:'ESModule',path:resolve(source,'views.mjs')}],modulesRoot:source,
    compatibilityDate:'2026-10-03',d1Databases:{DB:randomUUID()},
    d1Persist:resolve(process.env.SHOP_STATS_TEST_OUTPUT,'d1') }));
  db = await mf.getD1Database('DB');
  const migration = await readFile(resolve(source,'migrations/0001_reports.sql'),'utf8');
  const triggerStart = migration.indexOf('CREATE TRIGGER');
  const statements = migration.slice(0,triggerStart).replace(/^--.*$/gm,'').split(';').map(s => s.trim()).filter(Boolean);
  await db.batch(statements.map(sql => db.prepare(sql)));
  await db.prepare(migration.slice(triggerStart)).run();
});
beforeEach(async () => {
  await db.batch([db.prepare('DELETE FROM runs'),db.prepare(
    'UPDATE pools SET skystone_spent=0,covenant_purchases=0,mystic_purchases=0,updated_at=NULL')]);
});
after(async () => { if (mf) await mf.dispose(); });

test('fresh pools expose zero totals and null rates without fabricated update time', async () => {
  const value = await stats();
  assert.equal(value.updated_at,null);
  assert.deepEqual(Object.keys(value.pools).sort(),['continuous','transfer']);
  assert.equal(value.pools.transfer.covenant_per_100,null);
});

test('weighted totals include initial purchases and isolate strategies',async () => {
  await send(payload({skystone_spent:0,covenant_purchases:2}));
  await send(payload({skystone_spent:30,covenant_purchases:1}));
  await send(payload({skystone_spent:300,covenant_purchases:3,mystic_purchases:1}));
  await send(payload({strategy:'continuous',skystone_spent:60,covenant_purchases:2,mystic_purchases:1}));
  const value = await stats();
  assert.equal(value.pools.transfer.skystone_spent,330);
  assert.equal(value.pools.transfer.covenant_purchases,6);
  assert.equal(value.pools.transfer.covenant_per_100,600/330);
  assert.equal(value.pools.transfer.mystic_per_100,100/330);
  assert.equal(value.pools.continuous.skystone_spent,60);
  assert.match(value.updated_at,/Z$/);
});

test('lost acknowledgment and concurrent retries count exactly once',async () => {
  const data = payload();
  await send(data); // deliberately discard the first successful acknowledgment
  const results = await Promise.all(Array.from({length:8},() => send(data)));
  for (const response of results) {
    assert.equal(response.status,200);
    assert.deepEqual(await response.json(),{accepted:true,run_id:data.run_id});
  }
  assert.equal((await stats()).pools.transfer.skystone_spent,3);
  assert.equal((await db.prepare('SELECT COUNT(*) AS count FROM runs').first()).count,1);
});

test('same ID with different contents refuses overwrite during a race',async () => {
  const first = payload();
  const results = await Promise.all([send(first),send({...first,covenant_purchases:2})]);
  assert.deepEqual(results.map(r => r.status).sort(),[201,409]);
  const row = await db.prepare('SELECT * FROM runs WHERE run_id=?').bind(first.run_id).first();
  assert.equal((await stats()).pools.transfer.covenant_purchases,row.covenant_purchases);
  assert.equal((await stats()).pools.transfer.skystone_spent,3);
});

test('aggregate update failure rolls back the report insert',async () => {
  await db.prepare("CREATE TRIGGER fail_update BEFORE UPDATE ON pools BEGIN SELECT RAISE(ABORT,'injected'); END").run();
  try {
    const response = await send(payload());
    assert.equal(response.status,503);
    assert.equal((await db.prepare('SELECT COUNT(*) AS count FROM runs').first()).count,0);
    assert.equal((await stats()).pools.transfer.skystone_spent,0);
  } finally { await db.prepare('DROP TRIGGER fail_update').run(); }
});

test('rejects malformed numbers, foreign fields, strategies and impossible purchase counts',async () => {
  for (const change of [{skystone_spent:-3},{skystone_spent:4},{covenant_purchases:true},
    {mystic_purchases:1.5},{strategy:'other'},{version:'1.4.0'},{ended_at:'now'},
    {skystone_spent:0,covenant_purchases:7},{run_id:'old-short-id'}]) {
    const response = await send(payload(change));
    assert.ok([400,422].includes(response.status));
  }
  assert.equal((await db.prepare('SELECT COUNT(*) AS count FROM runs').first()).count,0);
});

test('body size limit is enforced even without a Content-Length header',async () => {
  const response = await mf.dispatchFetch('http://local/v1/shop-runs',{
    method:'POST',headers:{'Content-Type':'application/json'},body:' '.repeat(3000) });
  assert.equal(response.status,413);
});

test('anonymous rate limiting returns retry information before database work',async () => {
  const response = await worker.fetch(new Request('http://local/v1/shop-runs',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())}),
    {INGEST_LIMITER:{limit:async () => ({success:false})}});
  assert.equal(response.status,429);
  assert.equal(response.headers.get('Retry-After'),'60');
});

test('raw business table has only five fields and no public detail endpoint',async () => {
  const columns = await db.prepare('PRAGMA table_info(runs)').all();
  assert.deepEqual(columns.results.map(c => c.name),['run_id','strategy','skystone_spent','covenant_purchases','mystic_purchases']);
  assert.equal((await mf.dispatchFetch('http://local/runs')).status,404);
});

test('SVG and dashboard use safe headers, no external fonts or hover titles',async () => {
  await send(payload());
  const image = await mf.dispatchFetch('http://local/stats.svg');
  assert.match(image.headers.get('Content-Type'),/^image\/svg\+xml/);
  assert.equal(image.headers.get('Cache-Control'),'no-cache');
  const svg = await image.text();
  assert.match(svg,/转运池/);
  assert.match(svg,/33.33/);
  const pageResponse = await mf.dispatchFetch('http://local/stats');
  assert.match(pageResponse.headers.get('Content-Security-Policy'),/style-src 'self'/);
  assert.equal(pageResponse.headers.get('Cache-Control'),'no-cache');
  const page = await pageResponse.text();
  assert.match(page,/href="\/dashboard\.css\?v=[a-f0-9]+"/);
  assert.match(page,/src="\/dashboard\.js\?v=[a-f0-9]+"/);
  assert.match(page,/两种策略效果展示/);
  assert.match(page,/continuous-mystic_per_100-bar/);
  assert.equal((page.match(/class="pool"/g)??[]).length,2);
  assert.doesNotMatch(page,/此数据池用于观察整体获得效率|数据来自成功上报的匿名客户端/);
  assert.doesNotMatch(page,/<iframe|title="|\sstyle=|fonts\.google/i);
  assert.equal((await mf.dispatchFetch('http://local/stats.svg',{method:'HEAD'})).status,200);
});

test('dashboard preserves last valid totals on failure and skips hidden polling',async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id,{textContent:'—',style:{},disabled:false,addEventListener(){},setAttribute(){}});
    return elements.get(id);
  };
  let interval, calls = 0, fail = false;
  const document = {hidden:false,getElementById:element,addEventListener(){}};
  const value = await stats();
  const context = { document, AbortSignal, setInterval(callback) {interval=callback;},
    fetch:async () => {calls++; return {ok:!fail,json:async () => value};} };
  vm.runInNewContext(clientScript,context);
  await new Promise(resolve => setImmediate(resolve));
  const old = element('transfer-skystone_spent').textContent;
  assert.equal(old,'0');
  fail = true; interval();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('transfer-skystone_spent').textContent,old);
  assert.match(element('status').textContent,/保留上次/);
  const before = calls; document.hidden = true; interval();
  assert.equal(calls,before);
});
