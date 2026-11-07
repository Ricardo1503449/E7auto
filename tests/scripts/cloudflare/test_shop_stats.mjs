import assert from 'node:assert/strict';
import { test, before, beforeEach, after } from 'node:test';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import vm from 'node:vm';
import worker from '../../../scripts/cloudflare/shop_stats/worker.mjs';
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
const stats = async () => {
  const rows=(await db.prepare('SELECT * FROM pools').all()).results;
  return {pools:Object.fromEntries(rows.map(row=>[row.strategy,row])),updated_at:rows.map(row=>row.updated_at).filter(Boolean).sort().at(-1)??null};
};

before(async () => {
  mf = new Miniflare(convertV4MiniflareOptions({ modules:[
    {type:'ESModule',path:resolve(source,'worker.mjs')}],modulesRoot:source,
    compatibilityDate:'2026-10-03',d1Databases:{DB:randomUUID()},
    d1Persist:resolve(process.env.SHOP_STATS_TEST_OUTPUT,'d1') }));
  db = await mf.getD1Database('DB');
  const migration = await readFile(resolve(source,'migrations/0001_reports.sql'),'utf8');
  const triggerStart = migration.indexOf('CREATE TRIGGER');
  const statements = migration.slice(0,triggerStart).replace(/^--.*$/gm,'').split(';').map(s => s.trim()).filter(Boolean);
  await db.batch(statements.map(sql => db.prepare(sql)));
  await db.prepare(migration.slice(triggerStart)).run();
  const usage=await readFile(resolve(source,'migrations/0002_usage_days.sql'),'utf8');
  await db.batch(usage.split(';').map(value=>value.trim()).filter(Boolean).map(value=>db.prepare(value)));
});
beforeEach(async () => {
  await db.batch([db.prepare('DELETE FROM usage_days'),db.prepare('DELETE FROM runs'),db.prepare(
    'UPDATE pools SET skystone_spent=0,covenant_purchases=0,mystic_purchases=0,updated_at=NULL')]);
});
after(async () => { if (mf) await mf.dispose(); });

test('fresh pools expose zero totals and null rates without fabricated update time', async () => {
  const value = await stats();
  assert.equal(value.updated_at,null);
  assert.deepEqual(Object.keys(value.pools).sort(),['continuous','transfer']);
  assert.equal(value.pools.transfer.skystone_spent,0);
});

test('weighted totals include initial purchases and isolate strategies',async () => {
  await send(payload({skystone_spent:0,covenant_purchases:2}));
  await send(payload({skystone_spent:30,covenant_purchases:1}));
  await send(payload({skystone_spent:300,covenant_purchases:3,mystic_purchases:1}));
  await send(payload({strategy:'continuous',skystone_spent:60,covenant_purchases:2,mystic_purchases:1}));
  const value = await stats();
  assert.equal(value.pools.transfer.skystone_spent,330);
  assert.equal(value.pools.transfer.covenant_purchases,6);
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

const today=()=>new Date(Date.now()+8*3600000).toISOString().slice(0,10);
const usage=(values={})=>({installation_id:'a'.repeat(32),date:today(),version:'1.5.2',features:[],...values});
const sendUsage=value=>mf.dispatchFetch('http://local/v1/usage-days',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});

test('retired read routes and static assets return 404 without redirect or data',async()=>{
  for(const path of ['/','/stats','/stats.svg','/v1/shop-stats','/dashboard.css','/dashboard.js','/v1/statistics']) {
    for(const method of ['GET','HEAD','POST']) {
      const response=await mf.dispatchFetch('http://local'+path,{method});
      assert.equal(response.status,404);
      assert.equal(response.headers.get('Location'),null);
      assert.doesNotMatch(await response.text(),/pools|installation|https:/);
    }
  }
  const probe=await mf.dispatchFetch('http://local/v1/shop-runs',{method:'HEAD'});
  assert.equal(probe.status,405);assert.equal(probe.headers.get('Allow'),'POST');
});

test('daily usage merges duplicates and feature flags without version rollback or shop increments',async()=>{
  for(const value of [usage(),usage({features:['shop']}),usage({version:'1.5.1',features:['penguin']}),usage({features:['chaos']})]) {
    const response=await sendUsage(value); assert.equal(response.status,200);
    assert.deepEqual(await response.json(),{accepted:true,installation_id:value.installation_id,date:value.date});
  }
  await Promise.all(Array.from({length:5},()=>sendUsage(usage())));
  const result=(await db.prepare('SELECT * FROM usage_days').all()).results;
  assert.equal(result.length,1); assert.equal(result[0].features,7);
  assert.equal(result[0].version_patch,2);
  assert.equal((await stats()).pools.transfer.skystone_spent,0);
});

test('usage refuses unknown fields invalid identities future dates and foreign features',async()=>{
  for(const change of [{installation_id:'wrong'},{date:'2099-01-01'},{date:'2026-02-30'},
    {version:'1.5.2-beta'},{features:['shop','shop']},{features:['other']},{account:'forbidden'}]) {
    assert.ok([400,422].includes((await sendUsage(usage(change))).status));
  }
  assert.equal((await db.prepare('SELECT COUNT(*) AS count FROM usage_days').first()).count,0);
});

test('expired activity acknowledges without insertion and retention never deletes shop history',async()=>{
  const response=await sendUsage(usage({date:'2020-01-01'}));
  assert.equal((await response.json()).discarded,true);
  await send(payload()); await sendUsage(usage({features:['shop']}));
  await db.prepare("INSERT INTO usage_days VALUES (?,?,?,?,?,?,?)").bind('b'.repeat(32),'2020-01-01',1,5,2,1,'old').run();
  await worker.scheduled({}, {DB:db});
  assert.equal((await db.prepare('SELECT COUNT(*) AS count FROM usage_days').first()).count,1);
  assert.equal((await db.prepare('SELECT COUNT(*) AS count FROM runs').first()).count,1);
  assert.equal((await stats()).pools.transfer.skystone_spent,3);
});

test('activity limiter rejects before accessing the database',async()=>{
  const response=await worker.fetch(new Request('http://local/v1/usage-days',{method:'POST'}),
    {INGEST_LIMITER:{limit:async()=>({success:false})}});
  assert.equal(response.status,429);assert.equal(response.headers.get('Retry-After'),'60');
});
