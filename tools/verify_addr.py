#!/usr/bin/env python3.14
"""交叉验证: 用分区表计算偏移 vs 手工偏移, 并探测 super 真实位置"""
import os
import sys
from pathlib import Path
MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
(DUMP/"logs").mkdir(exist_ok=True)
(DUMP/".state").unlink(missing_ok=True)
sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk
from mtkclient.config.mtk_config import MtkConfig
from mtkclient.Library.Partitions.gpt import GptSettings
from mtkclient.Library.DA.mtk_da_handler import DaHandler

print("★ 请插线/上电 ★", flush=True)
cfg = MtkConfig(loglevel=20, gui=False); cfg.gpt_settings = GptSettings(0,0,0)
mtk = Mtk(config=cfg, loglevel=20)
if not mtk.preloader.init(display=True):
    print("!! 握手失败"); sys.exit(1)
dh = DaHandler(mtk, 20)
m2 = dh.connect(mtk)
if m2 is None or getattr(m2.daloader,"da",None) is None:
    m2 = dh.configure_da(m2 if m2 is not None else mtk)
dl = m2.daloader
print(f"\nDA: {type(dl.da).__name__} / {dl.daconfig.storage.flashtype}", flush=True)

page = cfg.pagesize
print(f"\n配置 pagesize = {page} (0x{page:x})", flush=True)

parts = dl.get_partition_data(parttype=None)
print(f"分区表 {len(parts)} 项\n", flush=True)
print(f"{'分区名':<20}{'sector':>14}{'sectors':>12}{'计算偏移':>16}", flush=True)
print("-"*64, flush=True)
targets = {}
for p in parts:
    n = getattr(p, "name", "")
    if n in ("super","boot_a","vbmeta_a","lk_a","userdata","boot_para"):
        off = p.sector * page
        targets[n] = (off, p.sectors * page)
        print(f"{n:<20}{p.sector:>14}{p.sectors:>12}{hex(off):>16}", flush=True)

print("\n=== 逐项验证 (读取头部 64 字节看是否非零) ===", flush=True)
for n,(off,size) in targets.items():
    out = DUMP / f"verify_{n}.bin"
    try:
        dl.readflash(addr=off, length=64, filename=str(out), parttype=None, display=False)
        d = out.read_bytes() if out.exists() else b""
        nz = sum(1 for b in d if b != 0)
        print(f"  {n:<12} @ {hex(off):>12}  前64字节非零={nz:<3}  {d[:16].hex()}", flush=True)
    except Exception as e:
        print(f"  {n:<12} @ {hex(off):>12}  异常: {e}", flush=True)

# 直接在 GPT 记录的位置读 super, 并再往前/往后扫一下
print("\n=== 手工从 0x3a800000 读 1MB, 找 LP 魔数 ===", flush=True)
out = DUMP / "super_probe.bin"
dl.readflash(addr=0x3A800000, length=0x100000, filename=str(out), parttype=None, display=False)
if out.exists():
    d = out.read_bytes()
    for kw in [b"LP_METADATA_GEOMETRY", b"LP_METADATA_HEADER", b"ANDROID", b"ext4", b"\x53\xef"]:
        i = d.find(kw)
        print(f"  {kw!r:24} -> {hex(i) if i!=-1 else '未找到'}", flush=True)
    nz = sum(1 for b in d if b != 0)
    print(f"  非零字节: {nz} / {len(d)}", flush=True)
