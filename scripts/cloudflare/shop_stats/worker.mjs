const fields = ['run_id','strategy','skystone_spent','covenant_purchases','mystic_purchases'];
const strategies = ['transfer','continuous'];
const counters = fields.slice(2);
const maxCounter = 2147483647;
const maxBody = 2048;

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), { status, headers: {
    'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', ...extra,
  }});
}

export function validateReport(value) {
  if (!value || Array.isArray(value) || typeof value !== 'object'
      || Object.keys(value).length !== fields.length
      || !fields.every(field => Object.hasOwn(value, field))) {
    throw new HttpError(400, 'invalid_fields');
  }
  if (typeof value.run_id !== 'string' || !/^[a-f0-9]{32}$/.test(value.run_id)
      || !strategies.includes(value.strategy)
      || !counters.every(field => Number.isInteger(value[field]) && value[field] >= 0 && value[field] <= maxCounter)
      || value.skystone_spent % 3 !== 0) {
    throw new HttpError(422, 'invalid_report');
  }
  // Six inventory slots per paid refresh, plus the initial inventory.
  if (value.covenant_purchases + value.mystic_purchases > 6 * (value.skystone_spent / 3 + 1)) {
    throw new HttpError(422, 'invalid_purchase_bound');
  }
  return value;
}

async function readBody(request) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('Content-Type') || '')) {
    throw new HttpError(400, 'json_required');
  }
  if (Number(request.headers.get('Content-Length')) > maxBody) throw new HttpError(413, 'body_too_large');
  const reader = request.body?.getReader();
  if (!reader) throw new HttpError(400, 'empty_body');
  const parts = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBody) { await reader.cancel(); throw new HttpError(413, 'body_too_large'); }
      parts.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.length; }
  let value;
  try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw new HttpError(400, 'invalid_json'); }
  return value;
}

async function insertReport(db, value) {
  const insertion = await db.prepare(`INSERT INTO runs (${fields.join(',')})
      VALUES (?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING`).bind(...fields.map(field => value[field])).run();
  const actual = await db.prepare(`SELECT ${fields.join(',')} FROM runs WHERE run_id=?`).bind(value.run_id).first();
  if (!actual) throw new Error('insert_unconfirmed');
  if (!fields.every(field => actual[field] === value[field])) throw new HttpError(409, 'run_id_conflict');
  return json({ accepted: true, run_id: value.run_id }, insertion.meta.changes ? 201 : 200);
}

const featureNames = ['shop','penguin','chaos'];
export function activityDay(now = Date.now()) {
  return new Date(now+8*60*60*1000).toISOString().slice(0,10);
}
export function validateUsage(value, today = activityDay()) {
  const names = ['installation_id','date','version','features'];
  if (!value || Array.isArray(value) || typeof value !== 'object'
      || Object.keys(value).length !== names.length || !names.every(name => Object.hasOwn(value,name))) {
    throw new HttpError(400,'invalid_fields');
  }
  if (typeof value.installation_id !== 'string' || !/^[a-f0-9]{32}$/.test(value.installation_id)
      || typeof value.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value.date)
      || typeof value.version !== 'string' || !/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(value.version)
      || !Array.isArray(value.features) || value.features.length > 3
      || new Set(value.features).size !== value.features.length
      || !value.features.every(name => featureNames.includes(name))) throw new HttpError(422,'invalid_usage');
  const timestamp = Date.parse(value.date+'T00:00:00Z');
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0,10) !== value.date
      || value.date > today) throw new HttpError(422,'invalid_activity_date');
  return value;
}
async function insertUsage(db, value) {
  const cutoff = activityDay(Date.now()-89*86400000);
  const acknowledgment = {accepted:true,installation_id:value.installation_id,date:value.date};
  if (value.date < cutoff) return json({...acknowledgment,discarded:true});
  const version = value.version.split('.').map(Number);
  const mask = value.features.reduce((result,name) => result | (1 << featureNames.indexOf(name)),0);
  const newer = '(excluded.version_major,excluded.version_minor,excluded.version_patch) > (usage_days.version_major,usage_days.version_minor,usage_days.version_patch)';
  await db.prepare(`INSERT INTO usage_days (installation_id,day,version_major,version_minor,version_patch,features,updated_at)
    VALUES (?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(installation_id,day) DO UPDATE SET
      features=usage_days.features | excluded.features,
      version_major=CASE WHEN ${newer} THEN excluded.version_major ELSE usage_days.version_major END,
      version_minor=CASE WHEN ${newer} THEN excluded.version_minor ELSE usage_days.version_minor END,
      version_patch=CASE WHEN ${newer} THEN excluded.version_patch ELSE usage_days.version_patch END,
      updated_at=excluded.updated_at`).bind(value.installation_id,value.date,...version,mask).run();
  return json(acknowledgment);
}
export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (!['/v1/shop-runs','/v1/usage-days'].includes(path)) return json({error:'not_found'},404);
    if (request.method !== 'POST') return json({error:'method_not_allowed'},405,{Allow:'POST'});
    try {
      const source = request.headers.get('CF-Connecting-IP') || 'local';
      if (env.INGEST_LIMITER && !(await env.INGEST_LIMITER.limit({key:source})).success) {
        return json({error:'rate_limited'},429,{'Retry-After':'60'});
      }
      const value = await readBody(request);
      return path === '/v1/shop-runs' ? await insertReport(env.DB,validateReport(value))
        : await insertUsage(env.DB,validateUsage(value));
    } catch(error) {
      return error instanceof HttpError ? json({error:error.message},error.status)
        : json({error:'temporarily_unavailable'},503,{'Retry-After':'60'});
    }
  },
  async scheduled(_event,env) {
    await env.DB.prepare('DELETE FROM usage_days WHERE day<?').bind(activityDay(Date.now()-89*86400000)).run();
  },
};
