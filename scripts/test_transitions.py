"""Local checks for transition indexing in the xfade chain.

The batched joiner (`_join_with_transitions`) and the older one-clip-at-a-time
chain (`_chain_incremental`) must choose the *same* transition for the same
join, or a video changes look the moment it is long enough to be batched --
which it always is in production. Both index joins 1-based by the incoming
clip, so a batch starting at clip N must not renumber from 1.

Encoding is stubbed out, so these checks need no ffmpeg and no GPU, and finish
in milliseconds. Run: python scripts/test_transitions.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

from pipeline import transitions as T  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


def collect_styles(n: int, style: str, batch_size: int) -> list[str]:
    """Return the transition name passed to each join, in clip order."""
    captured: list[str] = []
    original = (
        T.get_duration,
        T._encode_xfade_batch,
        T._concat_stream_copy,
        T.XFADE_BATCH_SIZE,
    )
    T.get_duration = lambda p: 4.0
    T.XFADE_BATCH_SIZE = batch_size
    T._concat_stream_copy = lambda paths, output: output

    def fake_encode(paths, actual_durations, styles, settings, output):
        captured.extend(styles)
        return output

    T._encode_xfade_batch = fake_encode
    try:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(transition=style, temp_dir=tmp, fps=30)
            rendered = [f"{tmp}/clip{i:03d}.mp4" for i in range(n)]
            T._join_with_transitions(rendered, [4.0] * n, settings)
    finally:
        (
            T.get_duration,
            T._encode_xfade_batch,
            T._concat_stream_copy,
            T.XFADE_BATCH_SIZE,
        ) = original
    return captured


def expected(n: int, style: str, batch_size: int) -> list[str]:
    """Transitions for the joins that actually exist, numbered globally.

    Clips are joined `batch_size` at a time and the batches are then
    stream-copied together, so the seam *between* batches is a hard cut with no
    transition -- that is documented in `_join_with_transitions`. Every other
    join is the `range(1, len(rendered))` join `_chain_incremental` would make,
    numbered by the incoming clip rather than by position within its batch.
    """
    out: list[str] = []
    for start in range(0, n, batch_size):
        idxs = range(start, min(start + batch_size, n))
        out.extend(T._transition_name(style, i) for i in list(idxs)[1:])
    return out


def batches_for(n: int, batch_size: int) -> int:
    return -(-n // batch_size)


def main() -> int:
    print("transition indexing:")

    for n in (2, 3, 8, 9, 16, 17, 20):
        got = collect_styles(n, "Auto", T.XFADE_BATCH_SIZE)
        want = expected(n, "Auto", T.XFADE_BATCH_SIZE)
        check(f"Auto picks the chain's transitions for {n} clips", got == want, f"got {got} want {want}")

    # The regression that mattered: batch 2 onwards must continue the global
    # numbering instead of restarting, and join 1 must not be skipped.
    got = collect_styles(20, "Auto", 8)
    check("first join is Auto's first transition", got[:1] == [T.AUTO_TRANSITIONS[0]], str(got[:1]))
    check(
        "join 8 (last in batch 1) is numbered globally",
        got[6] == T._transition_name("Auto", 7),
        f"join into clip 7 was {got[6]}",
    )
    check(
        "batch 2 continues the cycle, not a restart",
        got[7] == T._transition_name("Auto", 9),
        f"join into clip 9 was {got[7]}, want {T._transition_name('Auto', 9)}",
    )
    check(
        "every join is exactly one step along Auto's cycle",
        got == expected(20, "Auto", 8),
    )

    # A fixed style ignores indexing entirely, so batching cannot alter it.
    for n in (5, 14):
        got = collect_styles(n, "fade", 8)
        check(
            f"explicit 'fade' on every join for {n} clips",
            got == ["fade"] * (n - batches_for(n, 8)),
            str(got),
        )

    # Batch seams are hard cuts: n clips in b batches carry n - b transitions.
    for n in (9, 16, 20):
        got = collect_styles(n, "Auto", 8)
        check(
            f"{n} clips in batches of 8 carry {n - batches_for(n, 8)} joins",
            len(got) == n - batches_for(n, 8),
            str(len(got)),
        )

    # A single clip has no join at all, and must not be encoded or re-encoded.
    check("1 clip carries no transitions", collect_styles(1, "Auto", 8) == [])

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All transition checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
