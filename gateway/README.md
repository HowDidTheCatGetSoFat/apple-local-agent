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
- A conversation larger than the model's window is refused with HTTP 400 and
  `type: context_overflow`, before the model is loaded. This exists because the
  alternative was measured and is worse than an error: a backend handed a
  request past its window accepts it and never answers - 180 seconds with no
  reply, no error, nothing. A session that grew that way reads as a hung chat
  rather than a full one, and compacting cannot rescue it, since compacting
  sends the whole conversation. The refusal names the two numbers so the reply
  points at the only cure, which is a new session.

- The same tool call returning the same result `FXLLA_LOOP_LIMIT` times in a
  row (default 8, 0 disables) is refused with `type: tool_loop`, naming the
  call. A local model here ran one command 240 times over 8.5 hours: it started
  a server in the foreground, so the command could never exit and the result
  was identical every time. A model with no new information retrying is not a
  malfunction, it is the only move it has, so the stop has to come from
  outside it - and from here rather than from any one client, because the loop
  is a property of the conversation and every client sends the conversation
  here.

  The result is part of what is compared, not just the call. The same command
  with a CHANGING result is progress - polling a build, watching a file grow -
  and must never be mistaken for a loop. Only a run ending at the newest
  exchange counts, so a model that tried something else in between is left
  alone. The run is also remembered HERE, across requests, because a client
  that compacts replaces the tool history with a summary and the conversation
  then shows a run of one - the 240-attempt session contained two compactions,
  which landed outside the run by luck. The memory is bounded and expires after
  30 minutes, since the thing worth bounding is time burned.

  This fires around the eighth attempt rather than the 240th, and
  before the conversation has grown large enough to trip the size check below,
  which is why it is checked first: told about its size, someone starts a new
  session and loops again; told about the repetition, they look at the call.

  The token count is estimated from characters, so it is a check on the
  impossible rather than on the marginal: a request merely near the limit is
  still sent to the backend, which is the only thing that knows for certain. A
  model whose window cannot be read is never refused - no window is not a
  small window.
