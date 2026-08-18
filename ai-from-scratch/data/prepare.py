#!/usr/bin/env python3
"""Turn a plain-text training corpus into the binary token shards train.py
reads. This is the one step this codebase cannot do for you: you provide
the corpus (a directory of .txt files, or one big file), on the ground
that a from-scratch model is exactly as good as what you feed it and
picking that source is a real decision, not a default to bake in.

    python data/prepare.py --input path/to/corpus.txt --out_dir data/mycorpus
    python data/prepare.py --input path/to/text_dir/  --out_dir data/mycorpus

Writes train.bin and val.bin (uint16 token ids, memory-mapped by train.py)
plus meta.json recording the tokenizer and split sizes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

try:
    import tiktoken
    HAVE_TIKTOKEN = True
except ImportError:
    HAVE_TIKTOKEN = False


def iter_text(input_path: Path):
    if input_path.is_file():
        yield input_path.read_text(encoding="utf-8", errors="ignore")
        return
    for f in sorted(input_path.rglob("*.txt")):
        yield f.read_text(encoding="utf-8", errors="ignore")


def encode_gpt2(text: str) -> list[int]:
    enc = tiktoken.get_encoding("gpt2")
    return enc.encode_ordinary(text)


def encode_bytes(text: str) -> list[int]:
    # Fallback with zero dependencies: raw UTF-8 bytes as token ids (0-255).
    # Only pair this with config.DEBUG (vocab_size=256) - it is not
    # compatible with the 2.7B/2.1B configs, which assume GPT-2 BPE.
    return list(text.encode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path,
                     help="a .txt file, or a directory of .txt files")
    ap.add_argument("--out_dir", required=True, type=Path)
    ap.add_argument("--val_fraction", type=float, default=0.001,
                     help="held out for validation loss during training")
    ap.add_argument("--tokenizer", choices=["gpt2", "bytes"], default=None,
                     help="defaults to gpt2 if tiktoken is installed, else bytes")
    args = ap.parse_args()

    tokenizer = args.tokenizer or ("gpt2" if HAVE_TIKTOKEN else "bytes")
    if tokenizer == "gpt2" and not HAVE_TIKTOKEN:
        raise SystemExit("tiktoken is not installed: pip install tiktoken, or pass --tokenizer bytes")
    encode = encode_gpt2 if tokenizer == "gpt2" else encode_bytes
    dtype = np.uint16 if tokenizer == "gpt2" else np.uint8

    args.out_dir.mkdir(parents=True, exist_ok=True)

    ids: list[int] = []
    n_docs = 0
    for text in iter_text(args.input):
        ids.extend(encode(text))
        n_docs += 1
    if not ids:
        raise SystemExit(f"no text found under {args.input}")

    arr = np.array(ids, dtype=dtype)
    split = int(len(arr) * (1 - args.val_fraction))
    train_ids, val_ids = arr[:split], arr[split:]

    train_ids.tofile(args.out_dir / "train.bin")
    val_ids.tofile(args.out_dir / "val.bin")

    meta = {
        "tokenizer": tokenizer,
        "vocab_size": 50304 if tokenizer == "gpt2" else 256,
        "documents": n_docs,
        "train_tokens": int(len(train_ids)),
        "val_tokens": int(len(val_ids)),
    }
    (args.out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"tokenizer: {tokenizer}")
    print(f"documents: {n_docs}")
    print(f"train tokens: {len(train_ids):,}   val tokens: {len(val_ids):,}")
    print(f"wrote {args.out_dir}/train.bin, val.bin, meta.json")
    print()
    needed = 2_650_000_000 * 20  # Chinchilla-style ~20 tokens/parameter floor
    if len(train_ids) < needed:
        print(f"NOTE: {len(train_ids):,} training tokens is well under the "
              f"~{needed/1e9:.0f}B tokens a 2.65B-parameter model needs to be "
              "trained to completion (Hoffmann et al.'s ~20 tokens/parameter "
              "rule of thumb). It will still run and learn something at this "
              "size - useful for testing the pipeline - just don't expect a "
              "small corpus to produce a coherent model at 2.7B parameters.")


if __name__ == "__main__":
    main()
