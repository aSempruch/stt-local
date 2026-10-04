"""Plays one reply as a single continuous stream.

Run as ``python -m stt_local.player <sample_rate>`` with 16-bit mono PCM on stdin.
The output device opens on the first bytes and closes after stdin ends and the
audio drains, so it is held only while speaking. Opening a Bluetooth output costs
over a second, which a player per sentence paid between every sentence.

It is a separate process so PortAudio stays out of the app (see the recording
deadlocks) and stopping is a plain terminate.
"""

from __future__ import annotations

import os
import sys

READ_BYTES = 8192
# Silence written as the stream opens, so a waking Bluetooth link does not clip
# the first syllable.
LEAD_IN_SECONDS = 0.15


def play(fd: int, sample_rate: int) -> None:
    stream = None
    carry = b""
    try:
        while True:
            data = os.read(fd, READ_BYTES)
            if not data:
                break
            data = carry + data
            usable = len(data) - len(data) % 2
            carry = data[usable:]
            if not usable:
                continue
            if stream is None:
                import sounddevice as sd

                stream = sd.RawOutputStream(
                    samplerate=sample_rate, channels=1, dtype="int16"
                )
                stream.start()
                stream.write(bytes(int(sample_rate * LEAD_IN_SECONDS) * 2))
            stream.write(data[:usable])
    finally:
        if stream is not None:
            # stop() lets queued audio finish; abort() would cut the tail.
            stream.stop()
            stream.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    play(sys.stdin.fileno(), int(args[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
