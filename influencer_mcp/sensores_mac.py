"""Temperatura do Apple Silicon sem sudo, lida dos sensores HID do sistema (IOKit) com ctypes.

O `powermetrics` exige root; os sensores de temperatura (página de uso 0xff00, uso 5) são legíveis por qualquer
processo. Os sensores "tdie" são as temperaturas do chip (CPU e GPU dividem o mesmo die); "NAND" é o SSD.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
from functools import lru_cache

UTF8 = 0x08000100
TEMPERATURE_EVENT = 15


@lru_cache(maxsize=1)
def _api():
    iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    p, i64, i32 = ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32
    for function, restype, argtypes in (
        (cf.CFStringCreateWithCString, p, [p, ctypes.c_char_p, ctypes.c_uint32]),
        (cf.CFNumberCreate, p, [p, i32, p]),
        (cf.CFDictionaryCreate, p, [p, ctypes.POINTER(p), ctypes.POINTER(p), i64, p, p]),
        (cf.CFArrayGetCount, i64, [p]),
        (cf.CFArrayGetValueAtIndex, p, [p, i64]),
        (cf.CFStringGetCString, ctypes.c_bool, [p, ctypes.c_char_p, i64, ctypes.c_uint32]),
        (iokit.IOHIDEventSystemClientCreate, p, [p]),
        (iokit.IOHIDEventSystemClientSetMatching, None, [p, p]),
        (iokit.IOHIDEventSystemClientCopyServices, p, [p]),
        (iokit.IOHIDServiceClientCopyProperty, p, [p, p]),
        (iokit.IOHIDServiceClientCopyEvent, p, [p, i64, i32, i64]),
        (iokit.IOHIDEventGetFloatValue, ctypes.c_double, [p, i32]),
    ):
        function.restype, function.argtypes = restype, argtypes

    def string(text: str):
        return cf.CFStringCreateWithCString(None, text.encode(), UTF8)

    def number(value: int):
        holder = i32(value)
        return cf.CFNumberCreate(None, 3, ctypes.byref(holder))

    keys = (p * 2)(string("PrimaryUsagePage"), string("PrimaryUsage"))
    values = (p * 2)(number(0xFF00), number(5))
    client = iokit.IOHIDEventSystemClientCreate(None)
    iokit.IOHIDEventSystemClientSetMatching(client, cf.CFDictionaryCreate(None, keys, values, 2, None, None))
    return iokit, cf, client, string


def temperatures() -> dict[str, float]:
    """Nome do sensor -> °C (vários sensores podem repetir o nome; vale o maior). Vazio fora do Apple Silicon."""
    if sys.platform != "darwin":
        return {}
    try:
        iokit, cf, client, string = _api()
        services = iokit.IOHIDEventSystemClientCopyServices(client)
        result: dict[str, float] = {}
        product = string("Product")
        for index in range(cf.CFArrayGetCount(services)):
            service = cf.CFArrayGetValueAtIndex(services, index)
            name_ref = iokit.IOHIDServiceClientCopyProperty(service, product)
            buffer = ctypes.create_string_buffer(128)
            if not name_ref or not cf.CFStringGetCString(name_ref, buffer, 128, UTF8):
                continue
            event = iokit.IOHIDServiceClientCopyEvent(service, TEMPERATURE_EVENT, 0, 0)
            if event:
                value = iokit.IOHIDEventGetFloatValue(event, TEMPERATURE_EVENT << 16)
                if 0 < value < 150:
                    name = buffer.value.decode()
                    result[name] = max(value, result.get(name, 0.0))
        return result
    except (OSError, AttributeError, ValueError):
        return {}


def chip_temperature() -> float | None:
    """Maior temperatura do die do chip (°C), ou None se os sensores não responderem."""
    dies = [value for name, value in temperatures().items() if "tdie" in name]
    return round(max(dies), 1) if dies else None
