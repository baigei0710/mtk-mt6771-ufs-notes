#!/usr/bin/env python3.14
"""
写入"修复 recovery USB"的 boot 镜像 + 关闭校验的 vbmeta

写入内容:
  boot_a   <- boot_recovery_usb.img   (修了 UDC 路径 + 显式配置 adb)
  vbmeta_a <- 关闭 verity/verification (必须, 否则哈希不匹配无法启动)

回退: 用 backup/boot_a.bin 和 backup/vbmeta_a.bin 原样写回
      -> rollback.py 已备好

用法: perl -e 'alarm 600; exec @ARGV' /opt/homebrew/bin/python3.14 flash_recovery_usb.py
"""
import os
import hashlib
import struct
import sys
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP = DUMP / "backup"

NEW_BOOT = DUMP / "boot_recovery_usb.img"
NEW_VBMETA = DUMP / "test_vbmeta_patched.img"

(DUMP / "logs").mkdir(exist_ok=True)
(DUMP / ".state").unlink(missing_ok=True)

sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk                   # noqa: E402
from mtkclient.config.mtk_config import MtkConfig             # noqa: E402
from mtkclient.Library.Partitions.gpt import GptSettings      # noqa: E402
from mtkclient.Library.DA.mtk_da_handler import DaHandler     # noqa: E402


def verify_inputs():
    ok = True
    for f in (NEW_BOOT, NEW_VBMETA, BACKUP / "boot_a.bin", BACKUP / "vbmeta_a.bin"):
        if not f.exists():
            print(f"  ✗ 缺少 {f}", flush=True)
            ok = False
    if not ok:
        return False

    orig_b = (BACKUP / "boot_a.bin").read_bytes()
    new_b = NEW_BOOT.read_bytes()
    orig_v = (BACKUP / "vbmeta_a.bin").read_bytes()
    new_v = NEW_VBMETA.read_bytes()

    print(f"  boot  : {len(new_b)} 字节 (原 {len(orig_b)})", flush=True)
    print(f"    魔数 ANDROID!: {new_b[:8] == b'ANDROID!'}", flush=True)
    print(f"    大小一致: {len(new_b) == len(orig_b)}", flush=True)
    print(f"    AVBf 位置一致: {new_b.find(b'AVBf') == orig_b.find(b'AVBf')}", flush=True)
    print(f"    kernel 区一致: {new_b[0x800:0x93a000] == orig_b[0x800:0x93a000]}", flush=True)
    if not (new_b[:8] == b"ANDROID!" and len(new_b) == len(orig_b)
            and new_b.find(b"AVBf") == orig_b.find(b"AVBf")):
        print("  ✗ boot 镜像校验不通过", flush=True)
        ok = False

    flags = struct.unpack_from(">I", new_v, 0x78)[0]
    print(f"  vbmeta: {len(new_v)} 字节, flags@0x78 = {flags} "
          f"({'verity+verification 已关闭' if flags == 3 else '异常!'})", flush=True)
    if new_v[:4] != b"AVB0" or flags != 3:
        print("  ✗ vbmeta 校验不通过", flush=True)
        ok = False
    return ok


def main():
    print("=" * 66, flush=True)
    print(" 写入修复版 boot + 关闭校验的 vbmeta", flush=True)
    print("=" * 66, flush=True)

    print("\n=== 写入前校验 ===", flush=True)
    if not verify_inputs():
        print("\n!! 校验失败, 中止 (不写入任何内容)", flush=True)
        return 1

    print("\n★ 请给平板上电 / 重新插拔 USB ★", flush=True)
    cfg = MtkConfig(loglevel=20, gui=False)
    cfg.gpt_settings = GptSettings(0, 0, 0)
    mtk = Mtk(config=cfg, loglevel=20)
    if not mtk.preloader.init(display=True):
        print("\n!! 握手失败", flush=True)
        return 1
    print("\n★ 握手成功 ★", flush=True)

    dh = DaHandler(mtk, 20)
    m2 = dh.connect(mtk)
    if m2 is None or getattr(m2.daloader, "da", None) is None:
        m2 = dh.configure_da(m2 if m2 is not None else mtk)
    if m2 is None or getattr(m2.daloader, "da", None) is None:
        print("!! DA 装载失败", flush=True)
        return 1
    dl = m2.daloader
    print(f"  DA: {type(dl.da).__name__}", flush=True)

    pmap = {}
    for p in dl.get_partition_data(parttype=None):
        n = getattr(p, "name", "")
        if n in ("boot_a", "vbmeta_a"):
            pmap[n] = (p.sector * cfg.pagesize, p.sectors * cfg.pagesize)
    for n in ("boot_a", "vbmeta_a"):
        if n not in pmap:
            print(f"  ✗ 找不到分区 {n}", flush=True)
            return 1
        print(f"  {n:10} @ 0x{pmap[n][0]:x}  {pmap[n][1]} 字节", flush=True)

    jobs = [("boot_a", NEW_BOOT), ("vbmeta_a", NEW_VBMETA)]
    done = []
    for part, src in jobs:
        off, sz = pmap[part]
        data = src.read_bytes()
        if len(data) != sz:
            print(f"\n!! {part}: 镜像 {len(data)} != 分区 {sz}, 中止", flush=True)
            return 1
        print(f"\n>>> 写入 {part} ({len(data)} 字节) sha256="
              f"{hashlib.sha256(data).hexdigest()[:16]}", flush=True)
        r = dl.writeflash(addr=off, length=len(data), filename="", wdata=data,
                          display=True)
        print(f"    结果: {r}", flush=True)
        if not r:
            print(f"!! 写入 {part} 失败", flush=True)
            if done:
                print(f"!! 已完成: {done} — 建议立即回退 (rollback.py)", flush=True)
            return 1
        done.append(part)

    print("\n" + "=" * 66, flush=True)
    print(" ✓ 全部写入完成", flush=True)
    print("=" * 66, flush=True)
    print("\n回退命令 (若需要):", flush=True)
    print("  perl -e 'alarm 600; exec @ARGV' /opt/homebrew/bin/python3.14 "
          "~/tablet_dump/rollback.py", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
