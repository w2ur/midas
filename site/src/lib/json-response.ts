/** A prerendered JSON endpoint's response: pretty-printed, with a charset. */
export function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
