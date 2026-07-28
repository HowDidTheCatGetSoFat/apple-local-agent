# fxlla gateway

One OpenAI-compatible endpoint that fronts every downloaded model. Standard
library only. The CLI keeps ownership of load, download, and RAM; the gateway is
the router that orchestrates it.

## Pieces

- `fxlla_gateway.py` - the HTTP server. Aggregates downloaded models in
  `/v1/models`, and on each request routes to the model's backend, loading it on
  demand and evicting the least-recently-used backend when a load would exceed
  the RAM budget. Backends are launched through `fxlla _backend <alias> <port>`
  so the launch logic stays in the CLI.
- `metrics.py` - passive request metrics. Pure, standard-library helpers that
  derive time to first token and tokens per second from proxied traffic and
  append them to the stats time-series.

Driven through the CLI: `fxlla serve` / `fxlla unserve`. The endpoint defaults
to `127.0.0.1:8080`; keep the bind local unless you tunnel it with auth.

## Passive metrics

The gateway sees the real token stream as it proxies, so it measures usage
without a synthetic probe:

- Streamed responses (SSE): the first content delta marks time to first token,
  and deltas are counted for tokens per second (one token per delta,
  approximate). A trailing `usage` chunk (`stream_options.include_usage`), when
  present, overrides the count with the server's exact `completion_tokens`.
- Non-streamed responses: `usage.completion_tokens` is read from the body.

One sample per completed completion request is appended to `stats.jsonl` (the
same time-series `fxlla stats` and the menu bar app read), shaped as
`ts, model, engine, ram_mb, ttft_ms, tps` plus `source: "gateway"`. Recording is
best-effort: any failure is logged and swallowed so it never affects the
proxied response. Embeddings and non-generative endpoints are not timed.

## Notes and limits (v0)

- Token counts from streamed deltas are approximate unless the server emits a
  usage chunk. RAM is the backend process RSS at the moment the request ends.
- Cold start: the first request to an unloaded model pays the load time;
  resident hot models avoid it.
