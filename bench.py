#!/usr/bin/env python3
"""Decode throughput + GPU power from the retained event log. Zero dependencies.

Method (identical to the dashboard's efficiency chart):
- decode requests: `done` events with >= --min-tokens generated tokens (default 200)
- power: mean of `gpu` samples whose timestamp falls in [end - seconds, end + 2s]
  (2 s grace for one sampling interval); requests with < 5 samples are excluded
  from power/energy figures — those samples miss the request entirely, so their
  mean would idle-contaminate the result
- watts are GPU-only (nvidia-smi/NVML); CPU, RAM and screen are not included
"""
import glob, json, sys, bisect, statistics

def load(paths):
    dones, gpus, prefill = [], [], []
    for p in paths:
        try:
            with open(p, "r", errors="replace") as f:
                for line in f:
                    try: e = json.loads(line)
                    except ValueError: continue
                    if not isinstance(e, dict): continue
                    if e.get("type") == "done" and isinstance(e.get("tok_s"), (int, float)): dones.append(e)
                    elif e.get("type") == "gpu" and isinstance(e.get("p"), (int, float)): gpus.append(e)
                    elif e.get("type") == "prefill_sum" and isinstance(e.get("tok_s"), (int, float)): prefill.append(e["tok_s"])
        except OSError: pass
    return dones, gpus, prefill

def main(paths, min_tokens=200):
    dones, gpus, prefill = load(paths)
    gpus.sort(key=lambda e: e["ts"])
    ts = [e["ts"] for e in gpus]
    rows, idle = [], [e["p"] for e in gpus if isinstance(e.get("u"), (int, float)) and e["u"] < 5]
    for d in dones:
        if not (isinstance(d.get("tokens"), (int, float)) and d["tokens"] >= min_tokens): continue
        if not (isinstance(d.get("seconds"), (int, float)) and d["seconds"] > 0): continue
        lo, hi = d["ts"] - d["seconds"], d["ts"] + 2
        i, j = bisect.bisect_left(ts, lo), bisect.bisect_right(ts, hi)
        w = [gpus[k]["p"] for k in range(i, j)]
        if len(w) >= 5:
            rows.append((d["tok_s"], statistics.fmean(w), statistics.fmean(w) / d["tok_s"], d["tokens"]))
    if not rows:
        print("no decode requests with >= %d tokens and >= 5 power samples" % min_tokens); return 1
    rows.sort()
    n = len(rows)
    med = lambda vals: statistics.median(vals)
    p90 = sorted(r[0] for r in rows)[min(n - 1, int(round(0.9 * (n - 1))))]
    best = rows[-1]
    if prefill:
        print(f"prefill:        median {statistics.median(prefill):.0f} tok/s · peak {max(prefill):.0f} tok/s ({len(prefill)} requests)")
    print(f"decode requests kept: {n} (>= {min_tokens} generated tokens, >= 5 GPU power samples in window)")
    print(f"peak decode:    {best[0]:.1f} tok/s ({best[3]:.0f} tokens, {best[1]:.1f} W, {best[2]:.2f} J/token)")
    print(f"p90 decode:     {p90:.1f} tok/s")
    print(f"median decode:  {med(r[0] for r in rows):.1f} tok/s")
    print(f"W during decode: median {med(r[1] for r in rows):.1f} W")
    print(f"energy:         median {med(r[2] for r in rows):.2f} J/token · best {min(r[2] for r in rows):.2f} J/token")
    if idle:
        print(f"GPU idle draw:  {med(idle):.1f} W")
    return 0

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--min-tokens")]
    m = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--min-tokens=")]
    min_tokens = int(m[0]) if m else 200
    sys.exit(main(args or sorted(glob.glob("data/events.jsonl*")), min_tokens))
