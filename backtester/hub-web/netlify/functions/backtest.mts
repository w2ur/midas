// Proxies /api/backtest/* to the secured Cloud Run backtester.
// URL + shared secret live in Netlify env (BACKTESTER_URL, BACKTESTER_SECRET);
// neither ever reaches the public bundle. Mirrors subscribe.mts.

type Env = { BACKTESTER_URL?: string; BACKTESTER_SECRET?: string };

const MAX_BODY = 8192;

export function route(method: string, path: string): 'catalog' | 'run' | null {
  if (method === 'GET' && path.endsWith('/catalog')) return 'catalog';
  if (method === 'POST' && path.endsWith('/run')) return 'run';
  return null;
}

export async function handle(
  req: Request,
  env: Env,
  fetchFn: typeof fetch = fetch,
): Promise<Response> {
  const { BACKTESTER_URL: base, BACKTESTER_SECRET: secret } = env;
  if (!base || !secret) {
    console.error('BACKTESTER_URL / BACKTESTER_SECRET not set');
    return json(500, { error: 'server_misconfigured' });
  }

  const action = route(req.method, new URL(req.url).pathname);
  if (action === null) return json(404, { error: 'not_found' });

  const headers: Record<string, string> = { 'X-Backtester-Secret': secret };
  let init: RequestInit = { method: req.method, headers };

  if (action === 'run') {
    let body: string;
    try {
      body = JSON.stringify(await req.json());
    } catch {
      return json(400, { error: 'invalid_json' });
    }
    if (body.length > MAX_BODY) return json(413, { error: 'payload_too_large' });
    headers['Content-Type'] = 'application/json';
    init = { method: 'POST', headers, body };
  }

  let upstream: Response;
  try {
    upstream = await fetchFn(`${base}/${action}`, init);
  } catch (err) {
    console.error('backtester fetch failed', err);
    return json(502, { error: 'upstream_unreachable' });
  }

  if (upstream.status >= 500) {
    return json(502, { error: 'upstream_unreachable' });
  }

  const text = await upstream.text();
  return new Response(text || '{}', {
    status: upstream.status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export default async (req: Request): Promise<Response> =>
  handle(req, process.env as Env);
