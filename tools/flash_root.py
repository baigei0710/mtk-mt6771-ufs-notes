#!/usr/bin/env python3.14
"""
把 patch 好的 boot 镜像 + 关掉校验的 vbmeta 写入设备

⚠️ 写入操作! 回退方法见文件末尾.

用法: perl -e 'alarm 600; exec @ARGV' /opt/homebrew/bin/python3.14 flash_root.py
"""
import os
import struct
import sys
import time
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP = DUMP / "backup"

BOOT_PART = "boot_a"
VBMETA_PART = "vbmeta_a"
BOOT_PATCHED = DUMP / "test_boot_patched.img"
VBMETA_PATCHED = DUMP / "test_vbmeta_patched.img"

(DUMP / "logs").mkdir(exist_ok=True)
(DUMP / ".state").unlink(missing_ok=True)

sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk                   # noqa: E402
from mtkclient.config.mtk_config import MtkConfig             # noqa: E402
from mtkclient.Library.Partitions.gpt import GptSettings      # noqa: E402
from mtkclient.Library.DA.mtk_da_handler import DaHandler     # noqa: E402


def check_inputs():
    ok = True
    for f in (BOOT_PATCHED, VBMETA_PATCHED):
        if not f.exists():
            print(f"  ✗ 缺少 {f}", flush=True)
            ok = False
    for f in (BACKUP / "boot_a.bin", BACKUP / "vbmeta_a.bin"):
        if not f.exists():
            print(f"  ✗ 缺少回退备份 {f}", flush=True)
            ok = False
    if not ok:
        return False
    # 校验 patch 后的镜像
    b = BOOT_PATCHED.read_bytes()
    v = VBMETA_PATCHED.read_bytes()
    orig_b = (BACKUP / "boot_a.bin").read_bytes()
    print(f"  boot   镜像: {len(b)} 字节 (原 {len(orig_b)})", flush=True)
    if b[:8] != b"ANDROID!":
        print("  ✗ boot 魔数错误", flush=True); ok = False
    if len(b) != len(orig_b):
        print("  ✗ boot 大小与原始不一致", flush=True); ok = False
    if b.find(b"AVBf") != orig_b.find(b"AVBf"):
        print("  ✗ AVBf 位置偏移", flush=True); ok = False
    print(f"  vbmeta 镜像: {len(v)} 字节, flags@0x78 = "
          f"{struct.unpack_from('>I', v, 0x78)[0]}", flush=True)
    if v[:4] != b"AVB0":
        print("  ✗ vbmeta 魔数错误", flush=True); ok = False
    return ok


def main():
    print("=" * 66, flush=True)
    print(" 准备写入 boot_a (已 patch) + vbmeta_a (已关校验)", flush=True)
    print("=" * 66, flush=True)
    print("\n=== 写入前校验 ===", flush=True)
    if not check_inputs():
        print("\n!! 前置校验失败, 中止 (不会写入任何内容)", flush=True)
        return 1

    print("\n★ 请给平板上电 / 重新插拔 USB ★", flush=True)
    cfg = MtkConfig(loglevel=20, gui=False)
    cfg.gpt_settings = GptSettings(0, 0, 0)
    mtk = Mtk(config=cfg, loglevel=20)
    if not mtk.preloader.init(display=True):
        print("\n!! 握手失败", flush=True)
        return 1
    print(f"\n★ 握手成功 ★ is_brom={cfg.is_brom}", flush=True)

    dh = DaHandler(mtk, 20)
    m2 = dh.connect(mtk)
    if m2 is None or getattr(m2.daloader, "da", None) is None:
        m2 = dh.configure_da(m2 if m2 is not None else mtk)
    if m2 is None or getattr(m2.daloader, "da", None) is None:
        print("!! DA 装载失败", flush=True)
        return 1
    dl = m2.daloader
    print(f"  DA: {type(dl.da).__name__} / {dl.daconfig.storage.flashtype}", flush=True)

    parts = dl.get_partition_data(parttype=None)
    pmap = {}
    for p in parts:
        n = getattr(p, "name", "")
        if n:
            pmap[n] = (p.sector * cfg.pagesize, p.sectors * cfg.pagesize)
    for want in (BOOT_PART, VBMETA_PART):
        if want not in pmap:
            print(f"  ✗ 分区表里找不到 {want}", flush=True)
            return 1
        off, sz = pmap[want]
        print(f"  {want:12} @ 0x{off:x}  大小 {sz} ({sz/1024/1024:.1f} MB)", flush=True)

    # ---- 写入 boot_a ----
    off, sz = pmap[BOOT_PART]
    data = BOOT_PATCHED.read_bytes()
    if len(data) != sz:
        print(f"\n!! 镜像大小 {len(data)} 与分区 {sz} 不一致, 中止", flush=True)
        return 1
    print(f"\n>>> 写入 {BOOT_PART} ({len(data)} 字节) ...", flush=True)
    r = dl.writeflash(addr=off, length=len(data), filename="", wdata=data, display=True)
    print(f"    结果: {r}", flush=True)
    if not r:
        print("!! 写入 boot 失败", flush=True)
        return 1

    # ---- 写入 vbmeta_a ----
    off, sz = pmap[VBMETA_PART]
    vdata = VBMETA_PATCHED.read_bytes()
    print(f"\n>>> 写入 {VBMETA_PART} ({len(vdata)} 字节) ...", flush=True)
    r2 = dl.writeflash(addr=off, length=len(vdata), filename="", wdata=vdata, display=True)
    print(f"    结果: {r2}", flush=True)
    if not r2:
        print("!! 写入 vbmeta 失败 (boot 已写入!)", flush=True)
        print("!! 请立即用回退命令恢复: wo 0x20a00000 0x2000000 backup/boot_a.bin", flush=True)
        return 1

    print("\n" + "=" * 66, flush=True)
    print(" ✓ 写入完成", flush=True)
    print("=" * 66, flush=True)
    print("\n下一步:", flush=True)
    print("  1. 断电, 长按电源键开机", flush=True)
    print("  2. 等待进入系统 (可能比平时慢, 属正常)", flush=True)
    print("  3. 插 USB, 在 Mac 上跑: adb devices 然后 adb root", flush=True)
    print("\n回退 (若开不了机):", flush=True)
    print("  wo 0x20a00000 0x2000000 backup/boot_a.bin", flush=True)
    print("  wo 0x14500000 0x800000  backup/vbmeta_a.bin", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
