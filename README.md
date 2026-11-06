# Strata-qwen38fn-unslothGGUF

**llm-scope:** a local telemetry dashboard for **Strata** serving
**Qwen3.8-Flash-Next** using **Unsloth's GGUF**, specifically the
**UD-IQ2_XS** quantization in the reference setup.

## Inference engine and model

| Component | Reference setup |
|---|---|
| Inference engine | Strata |
| LLM | Qwen3.8-Flash-Next (shorthand: `qwen38-flash-next`) |
| GGUF publisher | Unsloth |
| GGUF source | [Unsloth / Qwen3.8-Flash-Next-GGUF](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) |
| Quantization | UD-IQ2_XS, distributed as three GGUF shards |
| Source verified as of | **2026-11-05** |

The upstream GGUF repository was created on **2026-08-26** and last updated on
**2026-11-04**, according to the Hugging Face model API when checked on
2026-11-05. These are repository timestamps, not a verified publication date
for each individual quantization. The GGUF was available **as of today's
verification date**; this README does not claim it was first released that day.

This repository provides telemetry tooling only. It does not bundle Strata,
model weights, or an inference-engine installer.

A zero-dependency Python collector and SVG dashboard for Strata inference logs,
with legacy Maya/GLM parsing support. It is read-only against the inference
engine and requires no API key. It observes all clients using the selected log,
not a single identified conversation.

## Run

```bash
git clone <repository-url>
cd Strata-qwen38fn-unslothGGUF
python3 llm-scope.py
```

Dashboard: **http://localhost:8089/**

The preferred default log is
`~/.local/share/strata/install/strata-unsloth-ud-iq2_xs.log`; if absent, the newest
`strata-*.log` in that directory is selected. Override with `--log` or
`LLM_SCOPE_LOG`. The selection is made once at startup; changing models to a
new log path requires restarting with that path.

```bash
# Background operation
setsid nohup python3 llm-scope.py --port 8089 > llm-scope.out 2>&1 &

# Optional settings
python3 llm-scope.py --log /path/to/engine.log --gpu 0 --disk nvme0n1 \
  --context-cap 65536 --poll 2

# Use a legacy GPU monitor log instead of nvidia-smi (not both)
python3 llm-scope.py --monitor /path/to/gpu_monitor_log.txt
```

`--poll` controls GPU/system sampling (default 2 seconds, minimum 0.2). The log
is checked every 0.5 seconds; the browser polls every 3 seconds. Faster polling
**cannot produce per-token telemetry that Strata does not emit**. Subprocess
and sampling duration add to the configured interval.

The server defaults to loopback, has no authentication, and does not enable
cross-origin API access. Use an SSH tunnel for remote access. A non-loopback
`--host` exposes telemetry to reachable clients; use only behind an
appropriate firewall/authenticated proxy. Logs can contain sensitive text.

## What the lanes mean

| Lane | Measurement |
|---|---|
| Prompt/context | Request prompt size, connected as a line; dashed red reference is `--context-cap` (match your engine configuration) |
| Prefill | Newly read tokens/second, amber bars and latest numeric value; reused tokens are not counted as newly processed tokens |
| Decode | Generated tokens/second averaged over a completed request; legacy Maya phase samples are also supported |
| MTP / suffix | Distinct colored draft acceptance points, not one conflated series |
| Expert cache | Decode expert-cache hit percentage |
| KV | Block reads hitting VRAM, with RAM transfer details in tooltips |
| GPU | Selected GPU utilization, temperature, power and memory from `nvidia-smi`; unavailable fields remain unknown |
| PSI-IO | System-wide `avg10` full I/O pressure, not instantaneous engine I/O |
| Disk | Selected Linux block-device read throughput, derived from sector counters |
| Dirty | System-wide dirty memory and writeback |
| Events | Engine lifecycle, cache operations, prompt reuse, warnings and legacy tool/refusal events |

**Timing limitation:** Strata writes prompt, prefill and decode metrics in a
summary after generation. Chart positions are collector observation times,
not phase start times. There is no live Strata decode line during generation.
Unknown chunk counts, finish reason, cancellation and request metadata are not
invented. A fully reused prompt has no measured prefill rate. Prompt drops
across clients are not automatically classified as compaction.

The collector starts at EOF and observes future lines. It handles replacement,
missing files, truncation and partial lines; replacement/truncated files are
read from their beginning. As with polling tailers, a truncate-and-regrow that
occurs entirely between polls and exceeds the previous offset cannot be
reliably detected. Lines written while the collector is stopped are not
backfilled, since untimestamped summaries cannot reconstruct historical timing.

## Retention and API

- Server memory: newest 200,000 events.
- JSONL: `data/events.jsonl` plus one rotated `.jsonl.1` backup, roughly 64 MiB
  total plus record-size overhead. Disk writes are serialized with event IDs.
- Startup streams history and skips malformed/incomplete records. Legacy
  records gain IDs, but earlier inaccurate metadata is not retroactively repaired.
- Browser: newest 50,000 events. “All retained” refers to this browser buffer.
- Data persists across process/box restarts when stored on persistent disk.

`GET /api/events?after=<event-id>` returns up to 2,000 events in ascending ID
order, plus `cursor`, `more`, `session`, `reset`, and `gap`. Continue with the
returned cursor while `more` is true. IDs—not timestamps—prevent ties from
losing events. A new server session requires restarting pagination from zero;
`gap` indicates expired server history. The old timestamp `since` cursor is
replaced by this interface.

`GET /api/state` returns counts within retained server history, last prompt,
latest I/O pressure and the configured context cap. These are not lifetime
counters or active-request counters.

## Tests

```bash
python3 -m unittest -v
node test_dashboard.cjs
```

Python tests cover parsing, optional lines, unknown metadata, event ordering,
pagination, persistence, corruption, concurrency, disk counters and file
tailing. The dependency-free Node harness checks chart scaling, the prompt
line, text escaping, empty/missing values, large histories and polling.

Files: `parser.py` (pure line parser), `llm-scope.py` (collector/API),
`index.html` (dashboard), `test_scope.py`, `test_dashboard.cjs`.
