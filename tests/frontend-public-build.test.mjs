import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const assets = join(root, 'apps/web/dist/assets');
const files = readdirSync(assets);
const bundle = files.filter(f => f.endsWith('.js')).map(f => readFileSync(join(assets, f), 'utf8')).join('\n');

test('public release excludes private workspace, Clerk and PDF worker chunks', () => {
  assert.ok(files.some(f => f.startsWith('PilotPage-')));
  assert.ok(!files.some(f => /PrivateRoot|RecordWorkspace|Auth-|pdf\.worker|App-/.test(f)));
  for (const privateCode of ['CLERK_PUBLISHABLE_KEY', '/api/auth/config', 'api.clerk.com', 'Opening your workspace', 'RecordWorkbench']) {
    assert.ok(!bundle.includes(privateCode), `Private code leaked: ${privateCode}`);
  }
});

test('pilot calendar targets the supplied event, with production direct-link rewrites', () => {
  assert.ok(bundle.includes('srikanth-samy-pfuse0/30min'));
  for (const location of ['vercel.json', 'apps/web/vercel.json']) {
    const config = JSON.parse(readFileSync(join(root, location), 'utf8'));
    assert.ok(config.rewrites.some(rule => rule.source === '/pilot' && rule.destination === '/index.html'));
    assert.equal(config.framework, 'vite');
  }
});
