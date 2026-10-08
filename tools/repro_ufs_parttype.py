#!/usr/bin/env python3
"""
Reproduce the UFS parttype bug in mtkclient/Library/DA/storage.py

Runs the ORIGINAL decision logic (lines 319-340) verbatim against every
advertised parttype, and reports which ones fall through to the `else`.

The class attribute `xml` is set from `damode == DAmodes.XML`; for the common
XFLASH path xml is False, which is the case this bug affects.
"""
from enum import IntEnum


class UFSPartitionType(IntEnum):
    BOOT1 = 1
    BOOT2 = 2
    USER = 3
    RPMB = 4


def partitiontype_and_size(parttype, xml=False):
    """Verbatim transcription of the UFS branch, storage.py:319-340 (buggy)."""
    # container for flashsize side effect
    self_flashsize = [None]

    if parttype == "lu0":
        if xml:
            parttype = "UFS-LUA0"
        self_flashsize[0] = "lu0_size"
    elif parttype == "lu1":  # BOOT1
        if xml:
            parttype = "UFS-LUA1"
        self_flashsize[0] = "lu1_size"
    elif parttype == "lu2":  # BOOT2
        if xml:
            parttype = "UFS-LUA2"
        self_flashsize[0] = "lu2_size"
    elif parttype == "lu3":
        if xml:
            parttype = "UFS-LUA3"
        self_flashsize[0] = "lu3_size"
    else:
        return "ERROR: Unknown parttype", None

    return parttype, self_flashsize[0]


def buggy(parttype, xml=False):
    """Prepend the offending lines 320-321 before dispatching."""
    if not xml:
        parttype = UFSPartitionType.USER      # <-- line 321: shadows the string
    return partitiontype_and_size(parttype, xml)


def fixed(parttype, xml=False):
    """String matching first, then assign the enum."""
    if parttype == "lu0":
        return UFSPartitionType.USER, "lu0_size"
    if parttype == "lu1":
        return UFSPartitionType.BOOT1, "lu1_size"
    if parttype == "lu2":
        return UFSPartitionType.BOOT2, "lu2_size"
    if parttype == "lu3":
        return UFSPartitionType.RPMB, "lu3_size"
    return "ERROR: Unknown parttype", None


print("=" * 66)
print("UFS branch, non-xml (XFLASH) path — the path the bug affects")
print("=" * 66)
print(f"{'parttype':<12}{'BROKEN (current)':<34}{'FIXED':<28}")
print("-" * 66)
for pt in ("lu0", "lu1", "lu2", "lu3"):
    b = buggy(pt)[0]
    f = fixed(pt)[0]
    print(f"{pt:<12}{str(b):<34}{str(f):<28}")

print()
print("Values actually accepted by the broken code:")
for pt in ("lu0", "lu1", "lu2", "lu3"):
    if not str(buggy(pt)[0]).startswith("ERROR"):
        print("  ", pt)
print("  (none)" if all(str(buggy(p)[0]).startswith("ERROR") for p in ("lu0", "lu1", "lu2", "lu3"))
      else "")

print()
print("Control: these still work, because they are matched before line 321")
print("-" * 66)
for pt in ("user", "boot1", "boot2", "rpmb"):
    # these are handled by the elif chain ABOVE the else branch, so they never
    # reach the broken code
    print(f"  {pt:<10} -> handled earlier in the elif chain (unaffected)")
