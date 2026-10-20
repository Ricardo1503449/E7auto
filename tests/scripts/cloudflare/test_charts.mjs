import assert from 'node:assert/strict';
import { test } from 'node:test';
import { poolModels,renderPoolSvg,renderSvg,dashboard,stylesheet,clientScript } from '../../../scripts/cloudflare/shop_stats/views.mjs';
import vm from 'node:vm';

function data() {
  return {updated_at:null,pools:{
    transfer:{skystone_spent:300,covenant_purchases:6,mystic_purchases:3,covenant_per_100:2,mystic_per_100:1},
    continuous:{skystone_spent:600,covenant_purchases:9,mystic_purchases:3,covenant_per_100:1.5,mystic_per_100:.5},
  }};
}

test('independent units and shared scales compare the two pools accurately',() => {
  const pools = poolModels(data());
  assert.deepEqual(pools.map(pool => pool.strategy),['transfer','continuous']);
  assert.deepEqual(pools.map(pool => pool.groups.map(group => group.maximum)),[[9,2],[9,2]]);
  assert.equal(pools[0].groups[0].rows[0].fraction,2/3);
  assert.equal(pools[0].groups[0].rows[1].fraction,1/3);
  assert.equal(pools[1].groups[1].rows[0].fraction,.75);
  assert.equal(pools[1].groups[1].rows[1].display,'0.50');
  assert.equal(pools[0].spend.display,'300');
});

test('zero and undefined rates never become fabricated nonzero bars',() => {
  const value = data();
  for (const pool of Object.values(value.pools)) {
    pool.skystone_spent=0;pool.covenant_purchases=0;pool.mystic_purchases=0;
    pool.covenant_per_100=null;pool.mystic_per_100=null;
  }
  const pools = poolModels(value);
  assert.ok(pools.flatMap(pool => pool.groups.flatMap(group => group.rows)).every(row => row.fraction===0));
  assert.ok(pools.every(pool => pool.spend.display==='0' && pool.groups[1].rows.every(row => row.display==='—')));
  assert.equal((renderSvg(value).match(/data-key="[^"]+"[^>]+width="0"/g)??[]).length,8);
  assert.doesNotMatch(renderSvg(value),/NaN|Infinity/);
  value.pools.transfer.covenant_purchases=2;
  assert.equal(poolModels(value)[0].groups[0].rows[0].fraction,1);
  assert.equal(poolModels(value)[0].groups[1].rows[0].display,'—');
});

test('exact names and rate labels match the requested copy across page and SVG',() => {
  assert.match(dashboard,/<h1>两种策略效果展示<\/h1>/);
  assert.match(dashboard,/转运池/);assert.match(dashboard,/连续刷新池/);
  assert.equal((dashboard.match(/class="pool"/g)??[]).length,2);
  assert.equal((renderSvg(data()).match(/<g data-pool=/g)??[]).length,2);
  for (const label of ['圣约书签购买次数 / 100天空石','神秘奖牌购买次数 / 100天空石']) {
    assert.equal(dashboard.split('<dt>'+label+'</dt>').length-1,2);
    assert.ok(renderSvg(data()).includes(label));
  }
  assert.doesNotMatch(dashboard,/默认转运策略池|连续刷新策略池|每100天空石 ·|此数据池用于观察|数据来自成功上报/);
  assert.doesNotMatch(dashboard,/title="|\sstyle=|<details|class="comparison"|模拟数据/);
  assert.doesNotMatch(dashboard,/<a\b|查看汇总 JSON/);
  assert.match(dashboard,/E7AUTO · 社区统计/);
  assert.match(stylesheet,/color:#000000;background:#f6f7f9/);
  for (const color of ['#5BC7A4','#73B6E6']) assert.ok(stylesheet.includes(color) && renderSvg(data()).includes(color));
});

test('pool SVG keeps all metrics and does not display an invalid aggregate timestamp',() => {
  const svg = renderPoolSvg(poolModels(data())[1]);
  assert.match(svg,/>9<\/text>/);
  assert.match(svg,/>0\.50<\/text>/);
  assert.match(svg,/aria-label="连续刷新池统计"/);
  assert.equal((svg.match(/data-key=/g)??[]).length,4);
  const value=data();value.updated_at='<script>alert(1)</script>';
  assert.doesNotMatch(renderSvg(value),/<script>|<title>/);
  assert.match(renderSvg(value),/数据更新时间（北京时间）：—/);
  assert.doesNotMatch(renderSvg(value),/alert\(1\)/);
});

test('SVG formats updates in Beijing minutes across day and year boundaries',() => {
  for (const [timestamp,display] of [
    ['2026-10-04T09:50:37.125Z','2026-10-04 17:50'],
    ['2026-10-04T16:00:59.999Z','2026-10-05 00:00'],
    ['2026-12-31T16:01:59.999Z','2027-01-01 00:01'],
  ]) {
    const value=data();value.updated_at=timestamp;
    const svg=renderSvg(value);
    assert.ok(svg.includes('数据更新时间（北京时间）：'+display));
    assert.ok(!svg.includes(timestamp));
  }
  assert.match(renderSvg(data()),/尚无成功上报的数据/);
});

test('invalid counts or rates cannot partially update either pool',() => {
  for (const bad of [null,-1,Infinity,NaN,1.5,true,undefined]) {
    const value=data();value.pools.continuous.skystone_spent=bad;
    assert.throws(() => poolModels(value),/invalid_pool_value/);
  }
  const value=data();value.pools.continuous.mystic_per_100=-1;
  assert.throws(() => poolModels(value),/invalid_pool_value/);
});

test('browser keeps totals and bars on network or malformed data failures and skips hidden polling',async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id,{textContent:'—',style:{width:'0%'},attributes:{},disabled:false,
      addEventListener(){},setAttribute(key,value){this.attributes[key]=value;}});
    return elements.get(id);
  };
  let interval, fail=false, malformed=false, calls=0, timestamp='2026-10-04T09:50:37.125Z';
  const document={hidden:false,getElementById:element,addEventListener(){}};
  vm.runInNewContext(clientScript,{document,AbortSignal,
    setInterval(callback,delay){assert.equal(delay,60000);interval=callback;},
    fetch:async () => {calls++;const value=data();value.updated_at=timestamp;if(malformed){value.pools.transfer.skystone_spent=999;value.pools.continuous.mystic_purchases=-1;}
      return {ok:!fail,json:async () => value};}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('transfer-skystone_spent').textContent,'300');
  assert.equal(element('continuous-mystic_per_100').textContent,'0.50');
  assert.equal(element('continuous-covenant_purchases-bar').style.width,'100%');
  assert.equal(element('updated').textContent,'数据更新时间（北京时间）：2026-10-04 17:50');
  const before=element('transfer-covenant_purchases-bar').style.width;
  assert.ok(Math.abs(parseFloat(before)-100*2/3)<1e-10);
  assert.match(element('transfer-covenant_purchases-track').attributes['aria-label'],/圣约书签购买次数：6/);
  fail=true;interval();await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('transfer-covenant_purchases-bar').style.width,before);
  assert.equal(element('updated').textContent,'数据更新时间（北京时间）：2026-10-04 17:50');
  assert.match(element('status').textContent,/保留上次/);
  fail=false;malformed=true;interval();await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('transfer-skystone_spent').textContent,'300');
  assert.equal(element('transfer-covenant_purchases-bar').style.width,before);
  assert.equal(element('updated').textContent,'数据更新时间（北京时间）：2026-10-04 17:50');
  malformed=false;timestamp='2026-10-04T16:00:59.999Z';interval();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('updated').textContent,'数据更新时间（北京时间）：2026-10-05 00:00');
  const prior=calls;document.hidden=true;interval();assert.equal(calls,prior);
});
