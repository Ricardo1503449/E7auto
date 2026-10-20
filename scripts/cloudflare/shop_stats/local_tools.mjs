// Test/development dependency lookup uses the same registry as Python tooling.
import { readFileSync } from 'node:fs';
import { resolve,relative } from 'node:path';
import { fileURLToPath } from 'node:url';

export function toolchainPath() {
  const root = fileURLToPath(new URL('../../../',import.meta.url));
  const policy = JSON.parse(readFileSync(new URL('../../project/layout.json',import.meta.url),'utf8'));
  const registry = policy.development_tools;
  const registered = resolve(root,registry.root,registry.installations.cloudflare_shop_stats.path);
  const selected = resolve(process.env.SHOP_STATS_TOOLCHAIN || registered);
  const normalize = value => process.platform === 'win32' ? value.toLowerCase() : value;
  if (relative(root,registered).startsWith('..') || normalize(selected) !== normalize(registered)) {
    throw new Error('Cloudflare dependencies must use the registered tools installation');
  }
  return selected;
}
