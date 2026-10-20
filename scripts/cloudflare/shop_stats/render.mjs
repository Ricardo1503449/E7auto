import { readFileSync } from 'node:fs';
import { renderSvg } from './views.mjs';
process.stdout.write(renderSvg(JSON.parse(readFileSync(0,'utf8'))));
