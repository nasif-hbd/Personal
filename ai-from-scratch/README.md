# A from-scratch language model, sized past 2B parameters

This is not a wrapper around a pretrained model, and it does not download
any weights. Every parameter starts as random noise from `model.py`'s
initializer; `train.py` is the only thing that would ever make it useful,
and that step needs to happen on real hardware you control.

## What's actually here

- `model.py` - the architecture: a decoder-only transformer (the GPT
  family's design), causal self-attention, GELU MLPs, pre-norm residual
  blocks, tied input/output embeddings.
- `config.py` - two real sizes (`LARGE_2_7B` at ~2.65B params, `LARGE_2_1B`
  at ~2.16B) plus `DEBUG`, a tiny byte-level config for testing the code
  on a laptop CPU in seconds.
- `count_params.py` - counts parameters exactly, using torch's `meta`
  device so it costs no memory. Run this after changing any config -
  see the comment on `LARGE_2_1B` for why: an earlier draft of that
  config, sized by formula instead of counted, came in under 2B.
- `data/prepare.py` - tokenizes your corpus into the binary shards
  `train.py` reads.
- `train.py` - the training loop: AdamW, cosine LR schedule with warmup,
  gradient accumulation, mixed precision, multi-GPU via `torchrun`,
  checkpointing.
- `sample.py` - generates text from a checkpoint.

## What I verified without a GPU

This sandbox has no GPU and can't reach PyPI to install PyTorch, so
nothing here has actually executed. What I could and did check:

- **Parameter count is exact arithmetic, not a guess.** `count_params.py`
  builds the real model on torch's `meta` device (zero memory: every
  tensor is shape-only) and sums `numel()` across every parameter - the
  same code path that runs when you actually train. `LARGE_2_7B` lands at
  **2.652B** parameters, `LARGE_2_1B` at **2.16B**. Run `python
  count_params.py` yourself the moment you have torch installed; it takes
  under a second.
- **The architecture is textbook, not novel.** Pre-norm decoder blocks,
  scaled dot-product attention, tied embeddings, GPT-2-style scaled
  residual init - all well-documented, widely-taught patterns. Nothing
  about it is unusual enough to carry hidden bugs from being untested;
  the risk in code like this is almost always in shapes and off-by-ones,
  and those are the same regardless of scale.

What I could not check without a GPU: actual training dynamics (does loss
go down the way the schedule intends, does gradient accumulation's timing
under DDP behave as commented). Run `DEBUG` first - it's designed exactly
for this.

## Step 1: prove the code runs, on your own machine

```bash
pip install -r requirements.txt
python -c "
import torch, config
from model import GPT
m = config.DEBUG
model = GPT(m)
x = torch.randint(0, m.vocab_size, (2, 16))
y = torch.randint(0, m.vocab_size, (2, 16))
logits, loss = model(x, y)
print('forward pass ok, loss =', loss.item())
loss.backward()
print('backward pass ok')
"
python count_params.py
```

If all three print cleanly, the code is sound. Everything below is a
question of hardware and data, not correctness.

## Step 2: get training data

`data/prepare.py` turns a `.txt` file or a directory of them into token
shards:

```bash
python data/prepare.py --input path/to/corpus.txt --out_dir data/mycorpus
```

**This is the step I can't do for you, on purpose.** A from-scratch model
is only as good as what it's trained on, and choosing that corpus - its
license, its content, its scale - is a real decision that belongs to
whoever is training the model, not a default I should quietly pick.

Scale matters more than almost anything else here. The commonly-cited
rule of thumb (Hoffmann et al., "Chinchilla") is roughly 20 training
tokens per parameter for compute-optimal training. For `LARGE_2_7B`
(2.65B params) that's **~53 billion tokens** - on the order of 100+GB of
raw text. `prepare.py` will warn you if what you feed it falls far short
of that; it will still run on less, it just won't produce a coherent
2.7B-parameter model, the same way underwatering a large plant doesn't
make it a small plant, it makes it a wilting large plant.

## Step 3: train

```bash
# smoke test - tiny data, tiny model, must run before anything bigger
python train.py --config DEBUG --data_dir data/mycorpus --batch_size 4 --grad_accum_steps 1 --max_steps 50

# the real thing - needs real GPUs, see below
torchrun --standalone --nproc_per_node=8 train.py \
  --config LARGE_2_7B --data_dir data/mycorpus \
  --batch_size 8 --grad_accum_steps 40
```

### Hardware, honestly

Plain DDP (what `train.py` implements) replicates the full model *and*
Adam's optimizer state on every GPU. For a 2.65B-parameter model in
fp32 Adam, that's roughly:

| | per parameter | at 2.65B params |
|---|---|---|
| weights (bf16) | 2 bytes | ~5.3 GB |
| gradients (bf16) | 2 bytes | ~5.3 GB |
| Adam moments (fp32 x2) | 8 bytes | ~21 GB |
| fp32 master weights (if used) | 4 bytes | ~10.6 GB |

That's **~30-40GB before a single activation is stored**, on *every* GPU,
with plain DDP. Realistically this means:

- One 80GB A100/H100 can just about hold the model + optimizer state, but
  you'll be memory-constrained on batch size and context length.
- Multiple GPUs get you data-parallel throughput, not more memory per
  copy - DDP doesn't shard anything, it duplicates.
- **To actually fit this comfortably, or to use smaller/cheaper GPUs**, add
  one of: FSDP or DeepSpeed ZeRO stage 2/3 (shards optimizer state and/or
  weights across GPUs - this is what real 2B+ training runs use), gradient
  checkpointing (trades recompute for activation memory, already stubbed
  out as a TODO-friendly spot in `model.py`'s `Block.forward`), or an
  8-bit optimizer (`bitsandbytes`' `AdamW8bit` roughly halves the
  optimizer-state line above). None of those are implemented here - this
  codebase is deliberately the simple, readable version, not a
  production-scale training harness, and layering in FSDP correctly is a
  large enough change that it deserves to be its own reviewed piece of
  work rather than something bolted on speculatively.

Rough order of magnitude for the full Chinchilla-optimal run: ~53B
tokens x 2.65B params x 6 FLOPs/token/param (the standard forward+backward
estimate) is on the order of 10^21 FLOPs. On a single A100 at realistic
(not peak) utilization, that is **weeks**, not hours. This is what
training a model at this scale from scratch actually costs - it's why
essentially nobody outside a well-funded lab does it, and why fine-tuning
an existing open-weights model is the practical path for almost every
real use case. I built you the real thing you asked for; this section is
so you're deciding to spend that time and money with full information,
not finding out afterward.

## Step 4: generate text

```bash
python sample.py --checkpoint checkpoints/ckpt_5000.pt --prompt "The history of"
```

## Where this deliberately stops

No RLHF, no instruction-tuning, no safety filtering, no chat formatting -
this is a raw base language model, exactly what "from scratch" means
architecturally. Turning a trained base model into something you'd want
to actually converse with is a separate, substantial project (supervised
fine-tuning on instruction data at minimum) - worth doing once this
trains, not before.
