import { test } from 'node:test';
import assert from 'node:assert/strict';
import { route, handle } from '../netlify/functions/backtest.mts';

const ENV = { BACKTESTER_URL: 'https://backend.example', BACKTESTER_SECRET: 's3cret' };

test('route maps method+path to the upstream action', () => {
  assert.equal(route('GET', '/api/backtest/catalog'), 'catalog');
  assert.equal(route('POST', '/api/backtest/run'), 'run');
  assert.equal(route('GET', '/api/backtest/run'), null);
  assert.equal(route('POST', '/api/backtest/catalog'), null);
  assert.equal(route('DELETE', '/api/backtest/catalog'), null);
});

test('missing env yields 500 server_misconfigured', async () => {
  const req = new Request('https://site/api/backtest/catalog');
  const res = await handle(req, {}, async () => new Response('{}'));
  assert.equal(res.status, 500);
  assert.deepEqual(await res.json(), { error: 'server_misconfigured' });
});

test('catalog GET injects the secret header and passes the body through', async () => {
  let seenUrl = '';
  let seenSecret = '';
  const fakeFetch = async (url: string | URL, init?: RequestInit) => {
    seenUrl = String(url);
    seenSecret = new Headers(init?.headers).get('X-Backtester-Secret') ?? '';
    return new Response(JSON.stringify({ presets: [] }), { status: 200 });
  };
  const req = new Request('https://site/api/backtest/catalog');
  const res = await handle(req, ENV, fakeFetch as unknown as typeof fetch);
  assert.equal(seenUrl, 'https://backend.example/catalog');
  assert.equal(seenSecret, 's3cret');
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { presets: [] });
});

test('run POST forwards the JSON body and a 400 passes through verbatim', async () => {
  let seenBody = '';
  const fakeFetch = async (_url: string | URL, init?: RequestInit) => {
    seenBody = String(init?.body ?? '');
    return new Response(JSON.stringify({ detail: 'Unknown universe' }), { status: 400 });
  };
  const req = new Request('https://site/api/backtest/run', {
    method: 'POST',
    body: JSON.stringify({ kind: 'signal' }),
    headers: { 'Content-Type': 'application/json' },
  });
  const res = await handle(req, ENV, fakeFetch as unknown as typeof fetch);
  assert.match(seenBody, /"kind":"signal"/);
  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { detail: 'Unknown universe' });
});

test('upstream 5xx collapses to 502 upstream_unreachable', async () => {
  const fakeFetch = async () => new Response('boom', { status: 500 });
  const req = new Request('https://site/api/backtest/run', {
    method: 'POST',
    body: '{}',
    headers: { 'Content-Type': 'application/json' },
  });
  const res = await handle(req, ENV, fakeFetch as unknown as typeof fetch);
  assert.equal(res.status, 502);
  assert.deepEqual(await res.json(), { error: 'upstream_unreachable' });
});

test('network failure collapses to 502', async () => {
  const fakeFetch = async () => { throw new Error('ECONNREFUSED'); };
  const req = new Request('https://site/api/backtest/catalog');
  const res = await handle(req, ENV, fakeFetch as unknown as typeof fetch);
  assert.equal(res.status, 502);
});

test('invalid JSON body on run yields 400 invalid_json', async () => {
  const req = new Request('https://site/api/backtest/run', {
    method: 'POST',
    body: 'not json',
    headers: { 'Content-Type': 'application/json' },
  });
  const res = await handle(req, ENV, async () => new Response('{}'));
  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { error: 'invalid_json' });
});

test('unroutable request yields 404', async () => {
  const req = new Request('https://site/api/backtest/other');
  const res = await handle(req, ENV, async () => new Response('{}'));
  assert.equal(res.status, 404);
});
