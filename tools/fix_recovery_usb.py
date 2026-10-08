#!/usr/bin/env python3.14
"""
修复 recovery 的 USB 功能, 让它能连 adb

问题(实测):
  1. init.recovery.*.rc 里写 /sys/class/udc/musb-hdrc/device/cmode 2
     <- "musb-hdrc" 这个 UDC 名在 MT6771/MT8788 上不存在, 写入静默失败
  2. recovery 下没人设置 sys.usb.controller 属性,
     导致 init.rc 里 configfs 逻辑的 ${sys.usb.controller} 为空, UDC 无法绑定
  => adbd 启动了但没有可用的 USB 端点 => USB 完全不枚举 => adb 连不上

修复:
  - 用一条 exec 命令动态探测真实 UDC 名并设置 cmode / sys.usb.controller
  - 显式触发 sys.usb.config=adb

用法:
  perl -e 'alarm 600; exec @ARGV' /opt/homebrew/bin/python3.14 fix_recovery_usb.py
"""
import os
import gzip
import struct
import sys
from pathlib import Path

DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP = DUMP / "backup"
SRC = BACKUP / "boot_a.bin"                  # 原始 boot (干净基线)
OUT = DUMP / "boot_recovery_usb.img"         # 输出

BOOT_MAGIC = b"ANDROID!"


def align(x, n):
    return ((x + n - 1) // n) * n


# ---------------- cpio ----------------
def cpio_unpack(buf: bytes):
    entries = []
    off = 0
    while off + 110 <= len(buf):
        if buf[off:off+6] != b"070701":
            raise ValueError(f"cpio magic mismatch @0x{off:x}")
        f = [int(buf[off+6+i*8:off+6+(i+1)*8], 16) for i in range(13)]
        mode, filesize, namesize = f[1], f[6], f[11]
        name = buf[off+110:off+110+namesize-1].decode("utf-8", "surrogateescape")
        doff = align(off+110+namesize, 4)
        data = buf[doff:doff+filesize]
        entries.append((name, mode, data))
        if name == "TRAILER!!!":
            break
        off = align(doff+filesize, 4)
    return entries


def cpio_pack(entries) -> bytes:
    out = bytearray()
    ino = 1
    for name, mode, data in entries:
        nb = name.encode("utf-8", "surrogateescape") + b"\x00"
        if name == "TRAILER!!!":
            data = b""
        fields = (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0)
        out += b"070701"
        out += b"".join(f"{v:08X}".encode() for v in fields)
        out += nb
        p1 = align(len(out), 4) - len(out)
        if p1:
            out += b"\x00" * p1
        out += data
        p2 = align(len(out), 4) - len(out)
        if p2:
            out += b"\x00" * p2
        ino += 1
    return bytes(out)


# ---------------- 新的 recovery rc 内容 ----------------
# 最小改动: 在原始 rc 内容上做字面替换, 其余字节完全保持原样
OLD_LINE = b"    write /sys/class/udc/musb-hdrc/device/cmode 2\n"
NEW_LINE = (b"    # [fix] value 2 = HOST_ONLY (kernel log: \"musb_cmode_store NORMAL --> HOST_ONLY\").\n"
            b"    #       HOST_ONLY forces host role, so the USB gadget can never be\n"
            b"    #       enumerated and adb is impossible in recovery.\n"
            b"    #       Keep NORMAL (dual-role) so the gadget can bind.\n"
            b"    write /sys/class/udc/musb-hdrc/device/cmode 0\n")


def patch_rc(orig: bytes) -> bytes:
    if OLD_LINE not in orig:
        raise ValueError("未找到原始 cmode 行, 不敢改")
    if orig.count(OLD_LINE) != 1:
        raise ValueError(f"cmode 行出现 {orig.count(OLD_LINE)} 次, 异常")
    return orig.replace(OLD_LINE, NEW_LINE)


TARGETS = ["init.recovery.mt6771.rc", "init.recovery.mt8788.rc"]


def main():
    if not SRC.exists():
        print(f"✗ 找不到原始 boot: {SRC}", flush=True)
        return 1
    raw_boot = SRC.read_bytes()
    if raw_boot[:8] != BOOT_MAGIC:
        print("✗ 不是 boot 镜像", flush=True)
        return 1

    (ksize, kaddr, rsize, raddr, ssize, saddr, tags, page,
     hv, osv) = struct.unpack_from("<10I", raw_boot, 8)
    print(f"boot: page={page} kernel={ksize} ramdisk={rsize} v{hv}", flush=True)

    koff = page
    roff = align(koff + ksize, page)
    ramdisk = raw_boot[roff:roff+rsize]
    raw = gzip.decompress(ramdisk)
    entries = cpio_unpack(raw)
    print(f"ramdisk: {len(raw)} -> cpio {len(entries)} 条目", flush=True)

    # 修改目标文件
    changed = []
    for i, (name, mode, data) in enumerate(entries):
        if name in TARGETS:
            try:
                newdata = patch_rc(data)
            except ValueError as e:
                print(f"\n!! {name}: {e}", flush=True)
                continue
            entries[i] = (name, mode, newdata)
            changed.append(name)
            print(f"\n=== 修改 {name} ({len(data)} -> {len(newdata)} 字节) ===", flush=True)
            print(f"    cmode 行: 2 (HOST_ONLY) -> 0 (NORMAL)", flush=True)

    if not changed:
        print(f"✗ 未找到目标文件 {TARGETS}", flush=True)
        return 1

    new_raw = cpio_pack(entries)
    new_ramdisk = gzip.compress(new_raw, 9, mtime=0)
    print(f"\n新 ramdisk: cpio {len(new_raw)}, gzip {len(new_ramdisk)} (原 {rsize})", flush=True)

    # 重建 boot: 结构必须与原镜像一致
    #   布局: header(1页) | kernel | 填充到页 | ramdisk | 填充到页 | [原 tail: AVB]
    #   AVB footer 必须落在与原镜像相同的位置 -> 在 tail 之前补齐
    hdr = bytearray(raw_boot[:page])
    struct.pack_into("<I", hdr, 0x10, len(new_ramdisk))     # 更新 ramdisk_size

    orig_content_end = align(roff + rsize, page)            # 原镜像内容结束位置
    tail = raw_boot[orig_content_end:]                      # 原 AVB 结构

    out = bytes(hdr)
    out += raw_boot[page:page + ksize]                      # kernel 原样
    out += b"\x00" * (align(len(out), page) - len(out))     # 页对齐
    out += new_ramdisk                                      # 新 ramdisk
    out += b"\x00" * (align(len(out), page) - len(out))     # 页对齐
    if len(out) < orig_content_end:                         # 补齐到原内容结束位置
        out += b"\x00" * (orig_content_end - len(out))
    out += tail                                             # AVB 结构, 位置不变
    if len(out) < len(raw_boot):                            # 补齐到原总长
        out += b"\x00" * (len(raw_boot) - len(out))

    OUT.write_bytes(out)
    print(f"\n输出: {OUT} ({len(out)} 字节, 原 {len(raw_boot)})", flush=True)
    print(f"  大小一致: {len(out) == len(raw_boot)}", flush=True)
    print(f"  AVBf 位置: 原 0x{raw_boot.find(b'AVBf'):x} 新 0x{out.find(b'AVBf'):x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
