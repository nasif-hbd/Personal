#!/usr/bin/env python3
"""Generate text from a checkpoint written by train.py.

    python sample.py --checkpoint checkpoints/ckpt_5000.pt --prompt "Once upon a time"
"""
from __future__ import annotations

import argparse

import torch

from model import GPT

try:
    import tiktoken
    HAVE_TIKTOKEN = True
except ImportError:
    HAVE_TIKTOKEN = False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--prompt", default="\n")
    ap.add_argument("--max_new_tokens", type=int, default=200)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--top_k", type=int, default=200)
    ap.add_argument("--num_samples", type=int, default=1)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    ckpt = torch.load(args.checkpoint, map_location=args.device)
    model = GPT(ckpt["config"])
    model.load_state_dict(ckpt["model"])
    model.to(args.device)
    model.eval()
    print(f"loaded checkpoint from step {ckpt['step']}, {model.num_params()/1e9:.3f}B params")

    vocab_size = ckpt["config"].vocab_size
    if vocab_size == 256:
        encode = lambda s: list(s.encode("utf-8"))
        decode = lambda ids: bytes(ids).decode("utf-8", errors="replace")
    else:
        if not HAVE_TIKTOKEN:
            raise SystemExit("pip install tiktoken to decode a gpt2-tokenizer checkpoint")
        enc = tiktoken.get_encoding("gpt2")
        encode = enc.encode_ordinary
        decode = enc.decode

    ids = torch.tensor([encode(args.prompt)], dtype=torch.long, device=args.device)
    for i in range(args.num_samples):
        out = model.generate(ids, args.max_new_tokens, temperature=args.temperature, top_k=args.top_k)
        print(f"--- sample {i + 1} ---")
        print(decode(out[0].tolist()))
        print()


if __name__ == "__main__":
    main()
