#!/usr/bin/env python3.14
"""
回退: 把 boot_a 和 vbmeta_a 恢复成原始备份

用法: perl -e 'alarm 600; exec @ARGV' /opt/homebrew/bin/python3.14 rollback.py
"""
import os
import hashlib
import sys
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP = DUMP / "backup"

# 用分区表查偏移更稳, 但为防万一也写死备用值
FALLBACK = {
    "boot_a":   (0x20A00000, 0x2000000),
    "vbmeta_a": (0x14500000, 0x800000),
}

(DUMP / "logs").mkdir(exist_ok=True)
(DUMP / ".state").unlink(missing_ok=True)

sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk                   # noqa: E402
from mtkclient.config.mtk_config import MtkConfig             # noqa: E402
from mtkclient.Library.Partitions.gpt import GptSettings      # noqa: E402
from mtkclient.Library.DA.mtk_da_handler import DaHandler     # noqa: E402


def main():
    print("=" * 66, flush=True)
    print(" 回退 boot_a + vbmeta_a 到原始备份", flush=True)
    print("=" * 66, flush=True)

    # 先确认备份可用
    blobs = {}
    for name in ("boot_a", "vbmeta_a"):
        f = BACKUP / f"{name}.bin"
        if not f.exists() or f.stat().st_size == 0:
            print(f"  ✗ 备份缺失: {f}", flush=True)
            return 1
        blobs[name] = f.read_bytes()
        print(f"  {name}.bin  {len(blobs[name])} 字节  "
              f"sha256={hashlib.sha256(blobs[name]).hexdigest()[:16]}", flush=True)

    print("\n★ 请给平板上电 / 重新插拔 USB ★", flush=True)
    cfg = MtkConfig(loglevel=20, gui=False)
    cfg.gpt_settings = GptSettings(0, 0, 0)
    mtk = Mtk(config=cfg, loglevel=20)
    if not mtk.preloader.init(display=True):
        print("\n!! 握手失败 — 设备没进 preloader 窗口", flush=True)
        print("   尝试: 长按电源 15 秒强制断电 -> 拔线 -> 不按键插回", flush=True)
        return 1
    print(f"\n★ 握手成功 ★", flush=True)

    dh = DaHandler(mtk, 20)
    m2 = dh.connect(mtk)
    if m2 is None or getattr(m2.daloader, "da", None) is None:
        m2 = dh.configure_da(m2 if m2 is not None else mtk)
    if m2 is None or getattr(m2.daloader, "da", None) is None:
        print("!! DA 装载失败", flush=True)
        return 1
    dl = m2.daloader
    print(f"  DA: {type(dl.da).__name__}", flush=True)

    # 取分区表偏移 (失败则用写死值)
    offs = dict(FALLBACK)
    try:
        for p in dl.get_partition_data(parttype=None):
            n = getattr(p, "name", "")
            if n in offs:
                offs[n] = (p.sector * cfg.pagesize, p.sectors * cfg.pagesize)
                print(f"  分区表: {n} @ 0x{offs[n][0]:x} 大小 {offs[n][1]}", flush=True)
    except Exception as e:
        print(f"  取分区表失败 ({e}), 使用备用偏移", flush=True)
        for n, (o, s) in FALLBACK.items():
            print(f"  备用: {n} @ 0x{o:x} 大小 {s}", flush=True)

    ok = True
    for name in ("boot_a", "vbmeta_a"):
        off, sz = offs[name]
        data = blobs[name]
        print(f"\n>>> 恢复 {name} @ 0x{off:x} ({len(data)} 字节) ...", flush=True)
        try:
            r = dl.writeflash(addr=off, length=len(data), filename="", wdata=data,
                              display=True)
            print(f"    结果: {r}", flush=True)
            if not r:
                ok = False
        except Exception as e:
            print(f"    异常: {type(e).__name__}: {e}", flush=True)
            ok = False

    print("\n" + "=" * 66, flush=True)
    print(" ✓ 回退完成" if ok else " ⚠️ 回退部分失败, 见上方输出", flush=True)
    print("=" * 66, flush=True)
    print("\n下一步: 断电, 长按电源键开机, 观察是否恢复正常", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
