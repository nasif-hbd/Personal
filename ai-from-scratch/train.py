#!/usr/bin/env python3
"""Pretraining loop for the model in model.py.

Single GPU:
    python train.py --config LARGE_2_7B --data_dir data/mycorpus

Multi-GPU (single node, N GPUs), the only way this is realistically usable
at 2.7B parameters:
    torchrun --standalone --nproc_per_node=8 train.py --config LARGE_2_7B --data_dir data/mycorpus

What this script does NOT do: shard optimizer state across GPUs (ZeRO/FSDP),
which is what makes 2B+ models actually fit in per-GPU memory during real
training. Plain DDP replicates the full model + Adam state (roughly 4x the
parameter count, in bytes, for fp32 Adam) on every GPU - at 2.7B params
that's on the order of 40GB per GPU before activations. If you don't have
that, see the README's "Making this fit on smaller hardware" section
before running the full config - it names the specific things to add
(FSDP, gradient checkpointing, 8-bit optimizer) rather than pretending
plain DDP will get you there.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import torch
import torch.distributed as dist

import config as cfgs
from model import GPT, GPTConfig


def get_batch(data_dir: Path, split: str, block_size: int, batch_size: int, device: str):
    # Re-opened per call rather than cached: np.memmap holds an open file
    # handle, and creating a fresh one avoids a slow memory leak some
    # platforms exhibit when the same memmap object lives for millions of
    # calls (see nanoGPT issue history - this is a known, deliberate choice,
    # not an oversight).
    meta = json.loads((data_dir / "meta.json").read_text())
    dtype = np.uint16 if meta["tokenizer"] == "gpt2" else np.uint8
    path = data_dir / ("train.bin" if split == "train" else "val.bin")
    data = np.memmap(path, dtype=dtype, mode="r")

    ix = torch.randint(len(data) - block_size, (batch_size,))
    x = torch.stack([torch.from_numpy(data[i:i + block_size].astype(np.int64)) for i in ix])
    y = torch.stack([torch.from_numpy(data[i + 1:i + 1 + block_size].astype(np.int64)) for i in ix])
    if device == "cuda":
        x, y = x.pin_memory().to(device, non_blocking=True), y.pin_memory().to(device, non_blocking=True)
    else:
        x, y = x.to(device), y.to(device)
    return x, y


def lr_schedule(step: int, warmup_steps: int, max_steps: int, max_lr: float, min_lr: float) -> float:
    if step < warmup_steps:
        return max_lr * (step + 1) / warmup_steps
    if step > max_steps:
        return min_lr
    decay_ratio = (step - warmup_steps) / max(1, max_steps - warmup_steps)
    coeff = 0.5 * (1.0 + math.cos(math.pi * decay_ratio))  # cosine decay to min_lr
    return min_lr + coeff * (max_lr - min_lr)


@torch.no_grad()
def estimate_loss(model, data_dir, block_size, batch_size, device, eval_iters: int):
    model.eval()
    out = {}
    for split in ("train", "val"):
        losses = torch.zeros(eval_iters)
        for k in range(eval_iters):
            x, y = get_batch(data_dir, split, block_size, batch_size, device)
            _, loss = model(x, y)
            losses[k] = loss.item()
        out[split] = losses.mean().item()
    model.train()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="LARGE_2_7B", choices=[n for n in dir(cfgs) if n.isupper()])
    ap.add_argument("--data_dir", required=True, type=Path,
                     help="directory containing train.bin/val.bin/meta.json from data/prepare.py")
    ap.add_argument("--out_dir", default=Path("checkpoints"), type=Path)
    ap.add_argument("--batch_size", type=int, default=8, help="per-GPU micro-batch size")
    ap.add_argument("--grad_accum_steps", type=int, default=40,
                     help="micro-batches accumulated before each optimizer step; "
                          "raise this instead of batch_size if you run out of memory")
    ap.add_argument("--max_steps", type=int, default=600_000)
    ap.add_argument("--warmup_steps", type=int, default=2_000)
    ap.add_argument("--max_lr", type=float, default=3e-4)
    ap.add_argument("--min_lr", type=float, default=3e-5)
    ap.add_argument("--weight_decay", type=float, default=0.1)
    ap.add_argument("--grad_clip", type=float, default=1.0)
    ap.add_argument("--eval_interval", type=int, default=500)
    ap.add_argument("--eval_iters", type=int, default=50)
    ap.add_argument("--log_interval", type=int, default=10)
    ap.add_argument("--resume", type=Path, default=None, help="checkpoint .pt to resume from")
    ap.add_argument("--compile", action="store_true", help="torch.compile the model (PyTorch 2.x)")
    args = ap.parse_args()

    # --- DDP setup, only if launched via torchrun -------------------------
    ddp = int(os.environ.get("RANK", -1)) != -1
    if ddp:
        dist.init_process_group(backend="nccl")
        ddp_rank = int(os.environ["RANK"])
        ddp_local_rank = int(os.environ["LOCAL_RANK"])
        ddp_world_size = int(os.environ["WORLD_SIZE"])
        device = f"cuda:{ddp_local_rank}"
        torch.cuda.set_device(device)
        is_master = ddp_rank == 0
    else:
        ddp_world_size = 1
        is_master = True
        device = "cuda" if torch.cuda.is_available() else "cpu"

    device_type = "cuda" if "cuda" in device else "cpu"
    torch.manual_seed(1337 + (int(os.environ.get("RANK", 0))))
    if device_type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

    if is_master:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        print(f"world_size={ddp_world_size}  device={device}  config={args.config}")

    gpt_config: GPTConfig = getattr(cfgs, args.config)
    model = GPT(gpt_config).to(device)
    if is_master:
        print(f"model: {model.num_params()/1e9:.3f}B parameters (excl. position embedding)")

    optimizer = model.configure_optimizer(
        weight_decay=args.weight_decay, learning_rate=args.max_lr,
        betas=(0.9, 0.95), device_type=device_type,
    )

    start_step = 0
    if args.resume:
        ckpt = torch.load(args.resume, map_location=device)
        model.load_state_dict(ckpt["model"])
        optimizer.load_state_dict(ckpt["optimizer"])
        start_step = ckpt["step"] + 1
        if is_master:
            print(f"resumed from {args.resume} at step {start_step}")

    if args.compile:
        model = torch.compile(model)

    raw_model = model
    if ddp:
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[int(os.environ["LOCAL_RANK"])])
        raw_model = model.module

    use_amp = device_type == "cuda"
    amp_dtype = torch.bfloat16 if (use_amp and torch.cuda.is_bf16_supported()) else torch.float16
    scaler = torch.cuda.amp.GradScaler(enabled=(use_amp and amp_dtype == torch.float16))

    t0 = time.time()
    for step in range(start_step, args.max_steps):
        lr = lr_schedule(step, args.warmup_steps, args.max_steps, args.max_lr, args.min_lr)
        for g in optimizer.param_groups:
            g["lr"] = lr

        if step % args.eval_interval == 0 and is_master:
            losses = estimate_loss(raw_model, args.data_dir, gpt_config.block_size,
                                    args.batch_size, device, args.eval_iters)
            print(f"step {step}: train loss {losses['train']:.4f}, val loss {losses['val']:.4f}")
            torch.save({
                "model": raw_model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "config": gpt_config,
                "step": step,
            }, args.out_dir / f"ckpt_{step}.pt")

        optimizer.zero_grad(set_to_none=True)
        loss_accum = 0.0
        for micro_step in range(args.grad_accum_steps):
            x, y = get_batch(args.data_dir, "train", gpt_config.block_size, args.batch_size, device)
            if ddp:
                # Only sync gradients on the final micro-step - the standard
                # gradient-accumulation-under-DDP trick, avoids an all-reduce
                # per micro-batch.
                model.require_backward_grad_sync = (micro_step == args.grad_accum_steps - 1)
            with torch.autocast(device_type=device_type, dtype=amp_dtype, enabled=use_amp):
                _, loss = model(x, y)
                loss = loss / args.grad_accum_steps
            loss_accum += loss.item()
            scaler.scale(loss).backward()

        if args.grad_clip > 0:
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(raw_model.parameters(), args.grad_clip)
        scaler.step(optimizer)
        scaler.update()

        if step % args.log_interval == 0 and is_master:
            dt = time.time() - t0
            t0 = time.time()
            tokens_per_step = args.batch_size * args.grad_accum_steps * gpt_config.block_size * ddp_world_size
            print(f"step {step}: loss {loss_accum:.4f}, lr {lr:.2e}, "
                  f"{dt*1000/args.log_interval:.0f}ms/iter, {tokens_per_step/dt:,.0f} tok/s")

    if ddp:
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
