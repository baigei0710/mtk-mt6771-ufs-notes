#!/usr/bin/env python3
"""
boot 镜像 ramdisk 补丁工具 (纯 Python, 无外部依赖)

用途: 在 Android boot 镜像的 ramdisk 里修改属性, 以获取 root 权限.

流程:
  unpack  -> 解出 boot 头/kernel/ramdisk
  patch   -> 解压 ramdisk(gzip) -> 解析 cpio -> 改 prop.default -> 重打包
  pack    -> 重建 boot 镜像 (页对齐, 重新 gzip)

注意: 修改后 boot 的哈希与 vbmeta 不符, 需同时 patch_vbmeta 关闭校验.
"""
import gzip
import hashlib
import io
import struct
import sys
from pathlib import Path

BOOT_MAGIC = b"ANDROID!"


# ---------------------------------------------------------------- boot 镜像
class BootImage:
    def __init__(self, data: bytes):
        self.raw = data
        if data[:8] != BOOT_MAGIC:
            raise ValueError("不是 Android boot 镜像 (缺 ANDROID! 魔数)")
        (self.kernel_size, self.kernel_addr,
         self.ramdisk_size, self.ramdisk_addr,
         self.second_size, self.second_addr,
         self.tags_addr, self.page_size,
         self.header_version, self.os_version) = struct.unpack_from("<10I", data, 8)
        self.header_size = 1632 if self.header_version >= 2 else 2048
        self.name = data[48:64]
        self.cmdline = data[64:64 + 512].split(b"\x00")[0]
        self.id = data[576:576 + 32]
        # v2: extra_cmdline 从 0x608 开始 (1024 字节), 之后还有若干字段.
        # 保守做法: 不自己重建 header, 直接整块保留原始 header 字节.
        self.header = data[:self.page_size]

        p = self.page_size
        self.kernel_off = p
        self.ramdisk_off = align(p + self.kernel_size, p)
        self.second_off = align(self.ramdisk_off + self.ramdisk_size, p)
        # 内容之后可能是 AVB 结构; 保留原样
        end = self.second_off + align(self.second_size, p) if self.second_size else \
            self.second_off
        self.tail = data[end:]

        self.kernel = data[self.kernel_off:self.kernel_off + self.kernel_size]
        self.ramdisk = data[self.ramdisk_off:self.ramdisk_off + self.ramdisk_size]
        self.second = data[self.second_off:self.second_off + self.second_size] \
            if self.second_size else b""

    def pack(self, ramdisk: bytes, kernel: bytes = None) -> bytes:
        """用新的 ramdisk 重建 boot 镜像, 结构与原镜像逐字节一致"""
        p = self.page_size
        kern = kernel if kernel is not None else self.kernel
        # 从原 header 整块复制, 只改动 ramdisk_size 字段 (偏移 0x10) 与
        # kernel_size (偏移 0x8, 万一 kernel 被替换). 其余字节原样保留,
        # 避免误清零 v2 的 extra_cmdline / recovery_dtbo / dtb 等字段.
        hdr = bytearray(self.header)
        struct.pack_into("<I", hdr, 0x08, len(kern))
        struct.pack_into("<I", hdr, 0x10, len(ramdisk))

        out = bytes(hdr) + pad(kern, p) + pad(ramdisk, p)
        if self.second_size:
            out += pad(self.second, p)
        # 关键: 在"内容"与"AVB tail"之间补齐, 使 AVB 结构落在与原镜像
        # 完全相同的位置 (AVB footer 必须在分区末尾的固定偏移).
        # 原镜像里 header/kernel/ramdisk 之后就是 tail 的起点.
        orig_tail_off = len(self.raw) - len(self.tail)
        if len(out) < orig_tail_off:
            out += b"\x00" * (orig_tail_off - len(out))
        out += self.tail
        if len(out) < len(self.raw):
            out += b"\x00" * (len(self.raw) - len(out))
        return out

    def describe(self) -> str:
        return (f"boot v{self.header_version} page={self.page_size} "
                f"kernel={self.kernel_size} ramdisk={self.ramdisk_size} "
                f"second={self.second_size}\n  cmdline: {self.cmdline.decode(errors='replace')}\n"
                f"  ramdisk@0x{self.ramdisk_off:x}  tail={len(self.tail)} 字节")


def align(x, n):
    return ((x + n - 1) // n) * n


def pad(data: bytes, n: int) -> bytes:
    r = len(data) % n
    return data + b"\x00" * (n - r) if r else data


# ---------------------------------------------------------------- cpio
CPIO_MAGIC = b"070701"


def cpio_unpack(buf: bytes) -> list:
    """解析 newc 格式 cpio, 返回 [(name, mode, data), ...]"""
    entries = []
    off = 0
    hdr_len = 110
    while off + hdr_len <= len(buf):
        if buf[off:off + 6] != CPIO_MAGIC:
            raise ValueError(f"cpio 魔数不符 @0x{off:x}: {buf[off:off+6]!r}")
        f = [int(buf[off + 6 + i * 8: off + 6 + (i + 1) * 8], 16) for i in range(13)]
        (ino, mode, uid, gid, nlink, mtime, filesize,
         devmajor, devminor, rdevmajor, rdevminor, namesize, check) = f
        name = buf[off + hdr_len: off + hdr_len + namesize - 1].decode("utf-8", "surrogateescape")
        doff = align(off + hdr_len + namesize, 4)
        data = buf[doff:doff + filesize]
        if name == "TRAILER!!!":
            entries.append((name, mode, b""))
            break
        entries.append((name, mode, data))
        off = align(doff + filesize, 4)
    return entries


def cpio_pack(entries: list) -> bytes:
    """按 newc 格式重新打包 cpio"""
    out = bytearray()
    ino = 1
    for name, mode, data in entries:
        nb = name.encode("utf-8", "surrogateescape") + b"\x00"
        if name == "TRAILER!!!":
            data = b""          # 不改 mode, 保持与原数据一致
        fields = (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0)
        out += CPIO_MAGIC
        out += b"".join(f"{v:08X}".encode() for v in fields)
        out += nb
        # 注意: 必须在写入之后再取长度做填充, 否则用到的是旧值 (变量遮蔽陷阱)
        pad_n = align(len(out), 4) - len(out)
        if pad_n:
            out += b"\x00" * pad_n
        out += data
        pad_d = align(len(out), 4) - len(out)
        if pad_d:
            out += b"\x00" * pad_d
        ino += 1
    # TRAILER 必须存在
    if not entries or entries[-1][0] != "TRAILER!!!":
        nb = b"TRAILER!!!\x00"
        fields = (ino, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(nb), 0)
        out += CPIO_MAGIC
        out += b"".join(f"{v:08X}".encode() for v in fields)
        out += nb
        out += b"\x00" * (align(len(out), 4) - len(out))
    return bytes(out)


# ---------------------------------------------------------------- 属性修改
def set_props(data: bytes, props: dict) -> bytes:
    """在属性文件里替换/追加属性, 返回新内容"""
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    seen = set()
    out = []
    for ln in lines:
        k = ln.split("=", 1)[0] if "=" in ln else None
        if k is not None and k in props:
            out.append(f"{k}={props[k]}")
            seen.add(k)
        else:
            out.append(ln)
    for k, v in props.items():
        if k not in seen:
            out.append(f"{k}={v}")
    return ("\n".join(out) + "\n").encode()


# ---------------------------------------------------------------- 主流程
def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("用法: bootpatch.py <原boot镜像> <输出的boot镜像>")
        return 1

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    img = BootImage(src.read_bytes())
    print("=== 原 boot 镜像 ===")
    print("  " + img.describe())
    print()

    # --- 自检 1: ramdisk gzip ---
    raw = gzip.decompress(img.ramdisk)
    print(f"=== ramdisk 解压: {len(raw)} 字节 ===")

    # --- 自检 2: cpio 往返一致性 ---
    entries = cpio_unpack(raw)
    print(f"=== cpio 条目: {len(entries)} ===")
    repacked = cpio_pack(entries)
    print(f"=== cpio 重打包: {len(repacked)} 字节 ===")
    if repacked != raw:
        print(f"  ⚠️ 重打包与原数据长度差异: {len(repacked)-len(raw):+d}")
        # 逐条比对, 找出差异来源
        re2 = cpio_unpack(repacked)
        if len(re2) != len(entries):
            print(f"  ✗ 条目数不一致: {len(re2)} vs {len(entries)}")
            return 1
        diff = 0
        for (n1, m1, d1), (n2, m2, d2) in zip(entries, re2):
            if n1 != n2 or m1 != m2 or d1 != d2:
                diff += 1
                if diff <= 3:
                    print(f"  ✗ 差异条目: {n1!r} mode={m1:o}/{m2:o} len={len(d1)}/{len(d2)}")
        if diff == 0:
            print("  ✓ 内容完全一致 (只是填充字节布局不同) — 可接受")
        else:
            print(f"  ✗ {diff} 个条目内容不一致 — 不可接受")
            return 1
    else:
        print("  ✓ cpio 往返完全一致")

    # --- 修改属性 ---
    props = {
        "ro.debuggable": "1",
        "ro.secure": "0",
        "ro.adb.secure": "0",
    }
    target = "prop.default"
    names = [e[0] for e in entries]
    if target not in names:
        print(f"\n✗ 找不到 {target}, 现有属性文件: "
              f"{[n for n in names if 'prop' in n.lower()][:10]}")
        return 1
    idx = names.index(target)
    old = entries[idx][2]
    new = set_props(old, props)
    print(f"\n=== 修改 {target} ({len(old)} -> {len(new)} 字节) ===")
    for k in props:
        before = next((l for l in old.decode(errors='replace').splitlines()
                       if l.startswith(k + "=")), "(无)")
        after = next((l for l in new.decode().splitlines() if l.startswith(k + "=")), "(无)")
        print(f"  {k:18} {before:28} -> {after}")
    entries[idx] = (entries[idx][0], entries[idx][1], new)

    # --- 重建 ---
    raw2 = cpio_pack(entries)
    comp = gzip.compress(raw2, 9, mtime=0)
    print(f"\n=== 新 ramdisk ===")
    print(f"  cpio: {len(raw2)} 字节   gzip: {len(comp)} 字节  (原 {len(img.ramdisk)})")
    if len(comp) > 16 * 1024 * 1024:
        print("  ✗ ramdisk 过大")
        return 1

    out = img.pack(comp)
    dst.write_bytes(out)
    print(f"\n=== 输出: {dst} ({len(out)} 字节) ===")
    print(f"  原镜像: {len(img.raw)} 字节   差值: {len(out)-len(img.raw):+d}")
    if len(out) > 32 * 1024 * 1024:
        print("  ⚠️ 超过 32MB 分区大小! 写入会失败")
        return 1

    # --- 验证输出可被重新解析 ---
    v = BootImage(out)
    vr = gzip.decompress(v.ramdisk)
    ve = cpio_unpack(vr)
    print(f"\n=== 输出验证 ===")
    print(f"  重新解析: kernel={v.kernel_size} ramdisk={v.ramdisk_size} "
          f"cpio条目={len(ve)}")
    ok = (v.kernel == img.kernel and len(ve) == len(entries))
    vi = [e[0] for e in ve]
    if target in vi:
        got = ve[vi.index(target)][2]
        for k, val in props.items():
            exp = f"{k}={val}".encode()
            if exp not in got:
                print(f"  ✗ {k} 未生效"); ok = False
    print(f"  kernel 一致: {v.kernel == img.kernel}")
    print(f"  {'✓ 输出验证通过' if ok else '✗ 输出验证失败'}")
    print(f"\n  SHA256: {hashlib.sha256(out).hexdigest()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
