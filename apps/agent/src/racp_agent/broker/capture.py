"""Bounded native DIB capture and PNG encoding; no global temporary files."""

import ctypes
import io
import math
import struct
import zlib
from ctypes import wintypes
from typing import Any

from PIL import Image
from racp_domain.models import RACPError

windows_ctypes: Any = ctypes
MAX_PIXELS = 16 * 1024 * 1024
MAX_PNG = 32 * 1024 * 1024


def png(width: int, height: int, bgra: bytes) -> bytes:
    if (
        not 0 < width <= 16384
        or not 0 < height <= 16384
        or width * height > MAX_PIXELS
        or len(bgra) != width * height * 4
    ):
        raise RACPError("RESOURCE_EXHAUSTED", "capture dimensions exceed limit", layer="broker")
    compressor = zlib.compressobj(6)
    compressed = bytearray()
    for row in range(height):
        pixels = bgra[row * width * 4 : (row + 1) * width * 4]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = pixels[2::4], pixels[1::4], pixels[0::4]
        compressed.extend(compressor.compress(b"\0" + rgb))
        if len(compressed) > MAX_PNG:
            raise RACPError("RESOURCE_EXHAUSTED", "capture PNG exceeds limit", layer="broker")
    compressed.extend(compressor.flush())

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    result = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", bytes(compressed))
        + chunk(b"IEND", b"")
    )
    if len(result) > MAX_PNG:
        raise RACPError("RESOURCE_EXHAUSTED", "capture PNG exceeds limit", layer="broker")
    return result


class BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("width", wintypes.LONG),
        ("height", wintypes.LONG),
        ("planes", wintypes.WORD),
        ("bits", wintypes.WORD),
        ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD),
        ("xppm", wintypes.LONG),
        ("yppm", wintypes.LONG),
        ("used", wintypes.DWORD),
        ("important", wintypes.DWORD),
    ]


class BitmapInfo(ctypes.Structure):
    _fields_ = [("header", BitmapHeader), ("colors", wintypes.DWORD * 3)]


def dib(source: int, left: int, top: int, width: int, height: int) -> bytes:
    """Copy a physical rectangle from a supplied DC to a top-down 32-bit DIB."""
    if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > MAX_PIXELS:
        raise RACPError("RESOURCE_EXHAUSTED", "capture dimensions exceed limit", layer="broker")
    gdi = windows_ctypes.WinDLL("gdi32", use_last_error=True)
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BitmapInfo),
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi.CreateDIBSection.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = (
        [wintypes.HDC, wintypes.HANDLE],
        wintypes.HANDLE,
    )
    gdi.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi.BitBlt.restype = wintypes.BOOL
    gdi.DeleteObject.argtypes, gdi.DeleteDC.argtypes = [wintypes.HANDLE], [wintypes.HDC]
    info = BitmapInfo(
        BitmapHeader(
            ctypes.sizeof(BitmapHeader), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0
        )
    )
    memory, bitmap, previous = gdi.CreateCompatibleDC(source), None, None
    if not memory:
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())
    try:
        bits = wintypes.LPVOID()
        bitmap = gdi.CreateDIBSection(source, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        previous = gdi.SelectObject(memory, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        if not gdi.BitBlt(
            memory, 0, 0, width, height, source, left, top, 0x40CC0020
        ):  # SRCCOPY | CAPTUREBLT
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        gdi.GdiFlush()
        return ctypes.string_at(bits, width * height * 4)
    finally:
        if previous and previous != ctypes.c_void_p(-1).value:
            gdi.SelectObject(memory, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)


def preview(width: int, height: int, pixels: bytes) -> tuple[bytes, dict[str, Any]]:
    if not 0 < width * height <= MAX_PIXELS or len(pixels) != width * height * 4:
        raise RACPError("RESOURCE_EXHAUSTED", "preview dimensions exceed limit", layer="broker")
    factor = min(1.0, 1600 / max(width, height))
    # High-entropy captures would repeatedly encode oversized previews. Sample RGB
    # across the image and start within the worst-case PNG envelope in that case.
    pieces = []
    for index in range(16):
        offset = (len(pixels) * index // 16) // 4 * 4
        sample = pixels[offset : offset + 4096]
        rgb = bytearray(len(sample) // 4 * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = sample[2::4], sample[1::4], sample[0::4]
        pieces.append(rgb)
    sample = b"".join(pieces)
    if sample and len(zlib.compress(sample, 1)) > len(sample) * 0.95:
        factor = min(factor, math.sqrt((2 * 1024 * 1024 - 65536) / (width * height * 3)))
    with Image.frombytes("RGB", (width, height), pixels, "raw", "BGRX") as image:
        for _ in range(12):
            pw, ph = max(1, int(width * factor)), max(1, int(height * factor))
            with image.resize((pw, ph), Image.Resampling.LANCZOS) as resized:
                stream = io.BytesIO()
                resized.save(stream, format="PNG", compress_level=6)
                encoded = stream.getvalue()
            if len(encoded) <= 2 * 1024 * 1024:
                return encoded, {
                    "width": pw,
                    "height": ph,
                    "physical_pixels_per_preview_pixel_x": width / pw,
                    "physical_pixels_per_preview_pixel_y": height / ph,
                }
            factor *= 0.75
    raise RACPError("RESOURCE_EXHAUSTED", "capture preview exceeds limit", layer="broker")
