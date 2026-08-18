"""Named model sizes. Import GPTConfig from model.py directly for anything
custom - these are just the two ends of the range you actually need:
one to prove the code works, one to prove the parameter count.
"""
from model import GPTConfig

# Runs on a laptop CPU in seconds. Use this to sanity-check the code (does
# a forward/backward pass work, does loss go down on a tiny batch) before
# ever touching a GPU bill.
DEBUG = GPTConfig(
    vocab_size=256,   # raw bytes as tokens - no tokenizer needed for a smoke test
    block_size=64,
    n_layer=2,
    n_head=2,
    n_embd=64,
    dropout=0.0,
    bias=True,
)

# The real target: matches GPT-3 2.7B's published shape (32 layers, 2560
# width, 32 heads) - a well-documented, known-trainable configuration,
# not a guess. Exact count is printed by count_params.py; it lands at
# ~2.65B parameters, comfortably over the 2B floor.
LARGE_2_7B = GPTConfig(
    vocab_size=50304,
    block_size=2048,
    n_layer=32,
    n_head=32,
    n_embd=2560,
    dropout=0.0,
    bias=True,
)

# A narrower alternative if 2.7B's memory footprint doesn't fit your
# hardware - same depth as the 2.7B config but a smaller width. Verified at
# ~2.16B params by count_params.py, not just estimated: an earlier, shallower
# draft of this config (26 layers) came in under 2B when actually counted,
# which is exactly why count_params.py exists rather than trusting a formula
# by eye - use it after touching any of these numbers.
LARGE_2_1B = GPTConfig(
    vocab_size=50304,
    block_size=2048,
    n_layer=32,
    n_head=24,
    n_embd=2304,
    dropout=0.0,
    bias=True,
)
