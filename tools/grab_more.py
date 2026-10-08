#!/usr/bin/env python3.14
"""
补充备份: para / md1img_a (基带) / proinfo 等 + 从 flash 抠出原厂 preloader

用法: perl -e 'alarm 300; exec @ARGV' /opt/homebrew/bin/python3.14 grab_more.py
复用已验证的握手逻辑: 先进入等待, 你插线后自动抓窗口.
"""
import os
import struct
import sys
import time
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP = DUMP / "backup"

# 补充清单: 体积极小但价值高
MORE = ["para", "md1img_a"]

BACKUP.mkdir(parents=True, exist_ok=True)
(DUMP / "logs").mkdir(exist_ok=True)
for f in (DUMP / ".state", BACKUP / ".state"):
    f.unlink(missing_ok=True)

sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk                      # noqa: E402
from mtkclient.config.mtk_config import MtkConfig                # noqa: E402
from mtkclient.Library.Partitions.gpt import GptSettings         # noqa: E402
from mtkclient.Library.DA.mtk_da_handler import DaHandler        # noqa: E402

print("=" * 62, flush=True)
print(" ★ 补充备份 —— 请给平板上电 / 重新插拔 USB ★", flush=True)
print("=" * 62, flush=True)

cfg = MtkConfig(loglevel=20, gui=False)
cfg.gpt_settings = GptSettings(0, 0, 0)
mtk = Mtk(config=cfg, loglevel=20)

if not mtk.preloader.init(display=True):
    print("\n!! 握手失败", flush=True)
    sys.exit(1)
print(f"\n★ 握手成功 ★ is_brom={cfg.is_brom}", flush=True)

dh = DaHandler(mtk, 20)
m2 = dh.connect(mtk)
if m2 is None or getattr(m2.daloader, "da", None) is None:
    m2 = dh.configure_da(m2 if m2 is not None else mtk)
if m2 is None or getattr(m2.daloader, "da", None) is None:
    print("!! DA 装载失败", flush=True)
    sys.exit(1)

dl = m2.daloader
print(f"  DA 装载完成: {type(dl.da).__name__} / {dl.daconfig.storage.flashtype}", flush=True)

entries = dl.get_partition_data(parttype=None)
print(f"  分区表: {len(entries)} 项\n", flush=True)

# ---------- 1) 补充分区 ----------
print("--- 补充分区 ---", flush=True)
for name in MORE:
    out = BACKUP / f"{name}.bin"
    part = next((e for e in entries
                 if getattr(e, "name", "").lower() == name.lower()), None)
    if part is None:
        print(f"  {name:12s} 无此分区", flush=True)
        continue
    addr = part.sector * cfg.pagesize
    length = part.sectors * cfg.pagesize
    print(f"  {name:12s} {length:>11} 字节 ...", end="", flush=True)
    try:
        dl.readflash(addr=addr, length=length, filename=str(out),
                     parttype=None, display=False)
        print(f" OK {out.stat().st_size}" if out.exists() else " FAIL", flush=True)
    except Exception as e:
        print(f" 异常 {e}", flush=True)

# ---------- 2) 从 flash 抠 preloader ----------
# LU0 头部; preloader 不在 GPT 里, 是 UFS boot region (mtkclient 的 --parttype=lu0 有 bug 用不了)
print("\n--- 搜索并抠出原厂 preloader ---", flush=True)
region = DUMP / "lu0_head8m.bin"
# parttype=None 在 UFS 上即 LUN0
try:
    dl.readflash(addr=0, length=0x800000, filename=str(region),
                 parttype=None, display=False)
except Exception as e:
    print(f"  读取 LU0 头部失败: {e}", flush=True)

if region.exists():
    d = region.read_bytes()
    print(f"  已读 LU0 头部: {hex(len(d))} 字节", flush=True)
    MMM = b"\x4d\x4d\x4d\x01\x38\x00\x00\x00"
    idx = d.find(MMM)
    if idx == -1:
        print("  未找到 MMM 魔数 (preloader 可能不在头部或被 OEM 整包写入)", flush=True)
        print(f"  头部 32 字节: {d[:32].hex()}", flush=True)
    else:
        print(f"  MMM 魔数位于: {hex(idx)}", flush=True)
        n = struct.unpack("<I", d[idx + 0x20:idx + 0x24])[0]
        print(f"  头部声明长度: {hex(n)}", flush=True)
        if 0x1000 < n < 0x800000:
            img = d[idx:idx + n]
            out = BACKUP / "preloader.bin"
            out.write_bytes(img)
            print(f"  ★ preloader.bin 已写出: {hex(len(img))}", flush=True)
            print(f"    UFS_BOOT={b'UFS_BOOT' in img}  EMMC_BOOT={b'EMMC_BOOT' in img}", flush=True)
            k = img.find(b"MTK_BLOADER_INFO")
            if k != -1:
                z = bytes([0])
                print(f"    bloader: {img[k:k + 0x28].split(z)[0].decode(errors='replace')}", flush=True)
                print(f"    原厂文件名: {img[k+0x1B:k+0x1B+0x30].split(z)[0].decode(errors='replace')}", flush=True)
        else:
            print("  声明长度不合理, 跳过", flush=True)

# ---------- 3) 汇总 ----------
print("\n" + "=" * 62, flush=True)
print("=== backup/ 最终清单 ===", flush=True)
import hashlib
tot = 0
for f in sorted(BACKUP.glob("*.bin")):
    d = f.read_bytes()
    tot += len(d)
    print(f"  {f.name:<18}{len(d):>11}  {hashlib.sha256(d).hexdigest()[:16]}", flush=True)
print(f"=== 共 {len(list(BACKUP.glob('*.bin')))} 个文件, {tot/1048576:.1f} MB ===", flush=True)
print(f"目录: {BACKUP}", flush=True)
