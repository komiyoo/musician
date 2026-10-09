"""Turn a Surge XT .fxp patch into a VST3 `raw_state` that pedalboard can load.

pedalboard exposes a VST3 plugin's state as JUCE's XML wrapper:

    b"VC2!" + <u32 little-endian length> + <?xml ...><VST3PluginState>
        <IComponent>{JUCE-base64 of the plugin's getStateInformation()}</IComponent>
    </VST3PluginState> + b"\\0"

Surge XT's getStateInformation() is exactly the patch chunk that a .fxp stores
after its 60-byte FXP header, so we can swap patches headlessly by name:
read .fxp -> strip header -> JUCE-base64 -> wrap -> plugin.raw_state = ...
"""
from __future__ import annotations

import re
import struct

_TABLE = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
_REV = {c: i for i, c in enumerate(_TABLE)}


def juce_b64encode(data: bytes) -> str:
    n = int.from_bytes(data, "little")
    num_chars = (len(data) * 8 + 5) // 6
    chars = [_TABLE[(n >> (6 * i)) & 63] for i in range(num_chars)]
    return f"{len(data)}." + "".join(chars)


def juce_b64decode(text: str) -> bytes:
    size_s, body = text.split(".", 1)
    size = int(size_s)
    n = 0
    for i, c in enumerate(body):
        n |= _REV[c] << (6 * i)
    return n.to_bytes(max(size, (len(body) * 6 + 7) // 8), "little")[:size]


def fxp_chunk(fxp: bytes) -> bytes:
    """Return the opaque chunk inside a VST2 'FPCh' .fxp (Surge patches)."""
    if fxp[:4] != b"CcnK" or fxp[8:12] != b"FPCh":
        raise ValueError("not an opaque-chunk .fxp")
    (chunk_size,) = struct.unpack(">i", fxp[56:60])
    return fxp[60:60 + chunk_size]


def split_state(raw_state: bytes) -> tuple[bytes, str]:
    """raw_state -> (IComponent bytes, full xml)"""
    xml = raw_state[8:].rstrip(b"\0").decode()
    m = re.search(r"<IComponent>(.*?)</IComponent>", xml, re.S)
    return juce_b64decode(m.group(1)), xml


def build_state(template_raw_state: bytes, component: bytes) -> bytes:
    """Replace the IComponent payload in an existing raw_state blob."""
    _, xml = split_state(template_raw_state)
    xml = re.sub(r"<IComponent>.*?</IComponent>",
                 lambda _: f"<IComponent>{juce_b64encode(component)}</IComponent>", xml, flags=re.S)
    payload = xml.encode() + b"\0"
    return template_raw_state[:4] + struct.pack("<I", len(payload)) + payload


def state_from_fxp(template_raw_state: bytes, fxp_path) -> bytes:
    with open(fxp_path, "rb") as f:
        chunk = fxp_chunk(f.read())
    return build_state(template_raw_state, chunk)
