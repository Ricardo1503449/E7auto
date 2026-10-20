import { dashboard, stylesheet, clientScript, renderSvg } from './views.mjs';

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

async function readReport(request) {
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
  return validateReport(value);
}

export async function getStats(db) {
  const { results } = await db.prepare('SELECT * FROM pools ORDER BY strategy').all();
  if (results.length !== 2 || !strategies.every(strategy => results.some(row => row.strategy === strategy))) {
    throw new Error('missing_pool');
  }
  const pools = {};
  let updated = null;
  for (const row of results) {
    if (!counters.every(field => Number.isSafeInteger(row[field]) && row[field] >= 0)) throw new Error('invalid_pool');
    pools[row.strategy] = Object.fromEntries(counters.map(field => [field, row[field]]));
    pools[row.strategy].covenant_per_100 = row.skystone_spent ? 100 * row.covenant_purchases / row.skystone_spent : null;
    pools[row.strategy].mystic_per_100 = row.skystone_spent ? 100 * row.mystic_purchases / row.skystone_spent : null;
    if (row.updated_at && (!updated || row.updated_at > updated)) updated = row.updated_at;
  }
  return { pools, updated_at: updated };
}

async function insertReport(db, value) {
  const insertion = await db.prepare(`INSERT INTO runs (${fields.join(',')})
      VALUES (?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING`).bind(...fields.map(field => value[field])).run();
  const actual = await db.prepare(`SELECT ${fields.join(',')} FROM runs WHERE run_id=?`).bind(value.run_id).first();
  if (!actual) throw new Error('insert_unconfirmed');
  if (!fields.every(field => actual[field] === value[field])) throw new HttpError(409, 'run_id_conflict');
  return json({ accepted: true, run_id: value.run_id }, insertion.meta.changes ? 201 : 200);
}

function asset(body, type, cache = 'public, max-age=300') {
  return new Response(body, { headers: {
    'Content-Type': `${type}; charset=utf-8`, 'Cache-Control': cache,
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
  }});
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/v1/shop-runs') {
        if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405, { Allow: 'POST' });
        // Anonymous source throttling is deliberately broad; shared NAT/proxy users can retry.
        // The key is transient and never written to D1/business records.
        const source = request.headers.get('CF-Connecting-IP') || 'local';
        if (env.INGEST_LIMITER && !(await env.INGEST_LIMITER.limit({ key: source })).success) {
          return json({ error: 'rate_limited' }, 429, { 'Retry-After': '60' });
        }
        return await insertReport(env.DB, await readReport(request));
      }
      if (!['GET','HEAD'].includes(request.method)) return json({ error: 'method_not_allowed' }, 405, { Allow: 'GET, HEAD' });
      let response;
      if (url.pathname === '/v1/shop-stats') response = json(await getStats(env.DB));
      else if (url.pathname === '/stats.svg') response = asset(renderSvg(await getStats(env.DB)), 'image/svg+xml', 'no-cache');
      else if (url.pathname === '/' || url.pathname === '/stats') response = asset(dashboard, 'text/html', 'no-cache');
      else if (url.pathname === '/dashboard.css') response = asset(stylesheet, 'text/css');
      else if (url.pathname === '/dashboard.js') response = asset(clientScript, 'text/javascript');
      else response = json({ error: 'not_found' }, 404);
      return request.method === 'HEAD' ? new Response(null, response) : response;
    } catch (error) {
      return error instanceof HttpError ? json({ error: error.message }, error.status)
        : json({ error: 'temporarily_unavailable' }, 503, { 'Retry-After': '60' });
    }
  },
};
