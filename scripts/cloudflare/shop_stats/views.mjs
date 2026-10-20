const poolDefinitions = [
  {strategy:'transfer',name:'转运池',color:'#5BC7A4'},
  {strategy:'continuous',name:'连续刷新池',color:'#73B6E6'},
];
const groupDefinitions = [
  {id:'purchases',title:'累计购买',keys:[
    ['covenant_purchases','圣约书签购买次数'],['mystic_purchases','神秘奖牌购买次数']]},
  {id:'efficiency',title:'获得效率',keys:[
    ['covenant_per_100','圣约书签购买次数 / 100天空石'],
    ['mystic_per_100','神秘奖牌购买次数 / 100天空石']]},
];

function escape(value) {
  return String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'})[char]);
}
function format(key,value) {
  return value === null ? '—' : key.endsWith('_per_100') ? value.toFixed(2) : value.toLocaleString('en-US');
}
function formatUpdatedAt(value) {
  if (!value) return '尚无成功上报的数据';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return '数据更新时间（北京时间）：—';
  // Read UTC parts after the fixed UTC+8 offset, independent of the viewer's zone.
  const date = new Date(timestamp+8*60*60*1000);
  return '数据更新时间（北京时间）：'+date.toISOString().slice(0,16).replace('T',' ');
}

// Each pool keeps five metrics; both pools share a zero-baseline scale per unit.
export function poolModels(stats) {
  const keys = ['skystone_spent',...groupDefinitions.flatMap(group => group.keys.map(([key]) => key))];
  for (const {strategy} of poolDefinitions) for (const key of keys) {
    const value = stats.pools?.[strategy]?.[key];
    if (key.endsWith('_per_100') && value === null) continue;
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 ||
        (!key.endsWith('_per_100') && !Number.isInteger(value))) throw new Error('invalid_pool_value');
  }
  const maxima = groupDefinitions.map(group => Math.max(0,...poolDefinitions.flatMap(({strategy}) =>
    group.keys.map(([key]) => stats.pools[strategy][key] ?? 0))));
  return poolDefinitions.map(definition => {
    const values = stats.pools[definition.strategy];
    return {...definition,spend:{value:values.skystone_spent,display:format('skystone_spent',values.skystone_spent)},
      groups:groupDefinitions.map((group,index) => ({id:group.id,title:group.title,maximum:maxima[index],
        rows:group.keys.map(([key,label]) => ({key,label,value:values[key],display:format(key,values[key]),
          fraction:maxima[index] ? (values[key] ?? 0)/maxima[index] : 0}))}))};
  });
}

export function renderPoolSvg(pool) {
  const content = pool.groups.map((group,index) => {
    const top = index ? 372 : 192;
    return `<line x1="24" x2="504" y1="${top-30}" y2="${top-30}" stroke="#e5e8ee"/>
      <text x="24" y="${top}" font-size="15" font-weight="600">${escape(group.title)}</text>`+
      group.rows.map((row,rowIndex) => {
        const y = top+38+rowIndex*66;
        return `<text x="24" y="${y}" font-size="14">${escape(row.label)}</text>
          <text x="504" y="${y}" text-anchor="end" font-size="${row.display.length>14 ? 18 : 22}" font-weight="500">${escape(row.display)}</text>
          <rect x="24" y="${y+11}" width="480" height="9" rx="3" fill="#eef0f5"/>
          <rect data-key="${row.key}" x="24" y="${y+11}" width="${480*row.fraction}" height="9" rx="3" fill="${pool.color}"/>`;
      }).join('');
  }).join('');
  const description = ['天空石总消耗：'+pool.spend.display,...pool.groups.flatMap(group =>
    group.rows.map(row => row.label+'：'+row.display))].join('；');
  return `<g data-pool="${pool.strategy}" role="img" aria-label="${escape(pool.name)}统计">
    <desc>${escape(description)}</desc>
    <rect width="528" height="536" rx="10" fill="#ffffff" stroke="#e5e8ee"/>
    <circle cx="28.5" cy="37.5" r="4.5" fill="${pool.color}"/>
    <text x="43" y="44" font-size="20" font-weight="600">${escape(pool.name)}</text>
    <text x="24" y="88" font-size="16">天空石总消耗</text>
    <text x="24" y="134" font-size="${pool.spend.display.length>20 ? 28 : 38}" font-weight="500">${escape(pool.spend.display)}</text>
    ${content}</g>`;
}

export function renderSvg(stats) {
  const pools = poolModels(stats);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1136" height="696" viewBox="0 0 1136 696" role="img" aria-label="两种策略效果展示">
    <desc>转运池和连续刷新池各展示五项累计指标；同类条形图在两池间使用相同比例。</desc>
    <rect width="1136" height="696" fill="#f6f7f9"/>
    <g font-family="Microsoft YaHei UI,Segoe UI,sans-serif" fill="#000000">
      <text x="24" y="35" fill="#228A50" font-size="13" font-weight="700" letter-spacing=".12em">E7AUTO · 社区统计</text>
      <text x="24" y="75" font-size="26" font-weight="600">两种策略效果展示</text>
      <text x="24" y="108" font-size="14">${escape(formatUpdatedAt(stats.updated_at))}</text>
      ${pools.map((pool,index) => `<g transform="translate(${24+index*560} 136)">${renderPoolSvg(pool)}</g>`).join('')}
    </g></svg>`;
}

const cards = poolDefinitions.map(({strategy,name}) => `<section class="pool" data-strategy="${strategy}" aria-labelledby="${strategy}-heading">
  <h2 id="${strategy}-heading"><span class="pool-dot" aria-hidden="true"></span>${name}</h2>
  <dl class="spend"><dt>天空石总消耗</dt><dd id="${strategy}-skystone_spent">—</dd></dl>
  ${groupDefinitions.map(group => `<section class="metric-group" aria-labelledby="${strategy}-${group.id}-heading">
    <h3 id="${strategy}-${group.id}-heading">${group.title}</h3><dl>
    ${group.keys.map(([key,label]) => `<div class="metric"><dt>${label}</dt><dd class="value" id="${strategy}-${key}">—</dd>
      <dd class="metric-chart"><span class="bar-track" id="${strategy}-${key}-track" role="img" aria-label="${label}：正在读取">
        <span class="bar" id="${strategy}-${key}-bar"></span></span></dd></div>`).join('')}
    </dl></section>`).join('')}
  </section>`).join('');

export const stylesheet = `:root{color-scheme:light;font-family:"Microsoft YaHei UI","PingFang SC","Segoe UI",sans-serif;color:#000000;background:#f6f7f9;font-size:15px;line-height:1.55}
  *{box-sizing:border-box}body{margin:0}main{max-width:1136px;margin:auto;padding:28px 24px 36px}
  h1{font-size:26px;font-weight:600;line-height:1.35;margin:0}.eyebrow{color:#228A50;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;font-size:13px;font-weight:700;letter-spacing:.12em;line-height:1.8;margin:1em 0}
  .toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:20px 0;font-size:14px}.toolbar p{margin:0}
  button{border:1px solid #d1d7df;border-radius:8px;background:#ffffff;color:#000000;min-height:44px;padding:10px 18px;font:inherit;cursor:pointer;white-space:nowrap}
  button:hover{background:#eef0f5}button:focus-visible{outline:3px solid #245fab;outline-offset:3px}button:disabled{opacity:.65;cursor:wait}
  .pools{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.pool{background:#ffffff;border:1px solid #e5e8ee;border-radius:10px;padding:24px;min-width:0}
  ${poolDefinitions.map(({strategy,color}) => `.pool[data-strategy="${strategy}"]{--pool-accent:${color}}`).join('')}
  .pool h2{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:600;margin:0 0 24px}.pool-dot{width:9px;height:9px;flex:none;background:var(--pool-accent);border-radius:50%}
  dl{margin:0}dd{margin:0}.spend{margin:0 0 24px}.spend dt{font-size:16px}.spend dd{font-size:38px;font-weight:500;line-height:1.25;margin-top:3px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
  .metric-group{border-top:1px solid #e5e8ee;padding-top:19px}.metric-group+.metric-group{margin-top:23px}.metric-group h3{font-size:15px;font-weight:600;margin:0 0 17px}
  .metric{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 14px;align-items:baseline}.metric+.metric{margin-top:19px}.metric dt{font-size:14px}
  .value{font-size:22px;font-weight:500;font-variant-numeric:tabular-nums;text-align:right;max-width:160px;overflow-wrap:anywhere}.metric-chart{grid-column:1/-1}
  .bar-track{display:block;width:100%;height:9px;background:#eef0f5;border-radius:3px;overflow:hidden}.bar{display:block;width:0%;height:100%;background:var(--pool-accent);border-radius:3px}
  #status{font-size:14px;margin:16px 0 0}#status:empty{display:none}
  @media(max-width:700px){main{padding:20px 12px 28px}.pools{grid-template-columns:1fr;gap:16px}.pool{padding:22px}h1{font-size:23px}.toolbar{align-items:flex-start}}
  @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}`;

export const clientScript = `(() => {
  const poolDefinitions = ${JSON.stringify(poolDefinitions)};
  const groupDefinitions = ${JSON.stringify(groupDefinitions)};
  const format = ${format.toString()};
  const formatUpdatedAt = ${formatUpdatedAt.toString()};
  const models = ${poolModels.toString()};
  const button = document.getElementById('refresh');
  const status = document.getElementById('status');
  let loading = false;
  async function refresh() {
    if (loading) return;
    loading = true; button.disabled = true;
    try {
      const response = await fetch('/v1/shop-stats',{cache:'no-store',signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('unavailable');
      const value = await response.json();
      const pools = models(value);
      for (const pool of pools) {
        document.getElementById(pool.strategy+'-skystone_spent').textContent = pool.spend.display;
        for (const group of pool.groups) for (const row of group.rows) {
          const id = pool.strategy+'-'+row.key;
          document.getElementById(id).textContent = row.display;
          document.getElementById(id+'-bar').style.width = 100*row.fraction+'%';
          document.getElementById(id+'-track').setAttribute('aria-label',row.label+'：'+row.display);
        }
      }
      document.getElementById('updated').textContent = formatUpdatedAt(value.updated_at);
      status.textContent = '';
    } catch { status.textContent = '暂时无法读取统计，保留上次显示结果；稍后自动重试。'; }
    finally { loading = false; button.disabled = false; }
  }
  button.addEventListener('click',refresh);
  document.addEventListener('visibilitychange',() => {if (!document.hidden) refresh();});
  setInterval(() => {if (!document.hidden) refresh();},60000);
  refresh();
})();`;

// Content-derived resource revisions keep cached scripts and styles in step.
function assetRevision(content) {
  let hash = 2166136261;
  for (let index=0;index<content.length;index++) {
    hash = Math.imul(hash ^ content.charCodeAt(index),16777619);
  }
  return (hash>>>0).toString(16);
}

export const dashboard = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1"><title>两种策略效果展示 · E7auto</title>
  <link rel="stylesheet" href="/dashboard.css?v=${assetRevision(stylesheet)}"><script defer src="/dashboard.js?v=${assetRevision(clientScript)}"></script></head><body>
  <main><header><p class="eyebrow">E7AUTO · 社区统计</p><h1>两种策略效果展示</h1></header>
  <div class="toolbar"><p id="updated">正在读取数据…</p><button id="refresh" type="button">刷新数据</button></div>
  <div class="pools">${cards}</div><p id="status" role="status" aria-live="polite"></p>
  </main></body></html>`;
