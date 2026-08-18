#!/usr/bin/env python3
"""Prints the exact parameter count for every config in config.py.

Uses torch's `meta` device, which allocates zero real memory - every tensor
exists only as shape metadata. That matters here: this script needs to run
BEFORE anyone commits to renting the GPUs a 2.7B model actually needs, and
it should give a real, counted answer rather than the formula-by-eye
estimate that (see config.py's comment on LARGE_2_1B) got this wrong once
already during development.

    python count_params.py
"""
import torch

import config as cfgs
from model import GPT

NAMES = [n for n in dir(cfgs) if n.isupper()]

for name in NAMES:
    cfg = getattr(cfgs, name)
    with torch.device("meta"):
        model = GPT(cfg)
    total = model.num_params(non_embedding=False)
    non_emb = model.num_params(non_embedding=True)
    flag = "OK  (clears 2B)" if total >= 2_000_000_000 else "under 2B"
    print(f"{name:12s}  {total/1e9:6.3f}B total  ({non_emb/1e9:.3f}B excl. position "
          f"embedding)  n_layer={cfg.n_layer} n_embd={cfg.n_embd} n_head={cfg.n_head}  {flag}")
