#!/usr/bin/env python3
"""
Zero-Width Unicode Codec

Encodes arbitrary bytes into invisible zero-width Unicode characters.
Uses base-4 encoding: 4 zero-width chars per byte.

Characters:
  U+200B Zero Width Space       = 0
  U+200C Zero Width Non-Joiner  = 1
  U+200D Zero Width Joiner      = 2
  U+FEFF Zero Width No-Break Sp = 3
"""

import os
import random

ZW = ["​", "‌", "‍", "﻿"]
ZW_SET = frozenset(ZW)
ZW_MAP = {c: i for i, c in enumerate(ZW)}

_S = "​​‌‌‍‍﻿﻿"
_E = "﻿﻿‍‍‌‌​​"

PAD_TARGET_BYTES = 1_048_576


def encode(data: bytes) -> str:
    out = []
    for b in data:
        out.append(ZW[b >> 6 & 3])
        out.append(ZW[b >> 4 & 3])
        out.append(ZW[b >> 2 & 3])
        out.append(ZW[b & 3])
    return "".join(out)


def decode(zw_text: str) -> bytes:
    chars = [c for c in zw_text if c in ZW_SET]
    result = bytearray()
    for i in range(0, len(chars) - 3, 4):
        b = (ZW_MAP[chars[i]] << 6
             | ZW_MAP[chars[i+1]] << 4
             | ZW_MAP[chars[i+2]] << 2
             | ZW_MAP[chars[i+3]])
        result.append(b)
    return bytes(result)


def _gen_padding(target_bytes: int = PAD_TARGET_BYTES) -> str:
    chars_needed = target_bytes // 3
    rng = random.Random(os.urandom(8))
    return "".join(rng.choice(ZW) for _ in range(chars_needed))


def inject(readme: str, payload: bytes, pad: bool = False) -> str:
    readme = strip(readme)
    blob = _S + encode(payload) + _E
    if pad:
        blob += _gen_padding()
    lines = readme.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("#"):
            lines[i] = line + blob
            return "\n".join(lines)
    if lines:
        lines[-1] = lines[-1] + blob
    else:
        lines.append(blob)
    return "\n".join(lines)


def strip(readme: str) -> str:
    return "".join(c for c in readme if c not in ZW_SET)


def extract(readme: str) -> bytes | None:
    start = readme.find(_S)
    if start == -1:
        return None
    end = readme.find(_E, start + len(_S))
    if end == -1:
        return None
    return decode(readme[start + len(_S):end])
