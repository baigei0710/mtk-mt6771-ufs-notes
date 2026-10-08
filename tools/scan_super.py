#!/usr/bin/env python3.14
"""扫描 super 分区, 定位谁占了多少空间 (只读)"""
import os
import sys
from pathlib import Path
MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient"))); DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
(DUMP/"logs").mkdir(exist_ok=True); (DUMP/".state").unlink(missing_ok=True)
sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk
from mtkclient.config.mtk_config import MtkConfig
from mtkclient.Library.Partitions.gpt import GptSettings
from mtkclient.Library.DA.mtk_da_handler import DaHandler

print("★ 请插线/上电 ★", flush=True)
cfg = MtkConfig(loglevel=20, gui=False); cfg.gpt_settings = GptSettings(0,0,0)
mtk = Mtk(config=cfg, loglevel=20)
if not mtk.preloader.init(display=True): print("!! 握手失败"); sys.exit(1)
dh = DaHandler(mtk, 20); m2 = dh.connect(mtk)
if m2 is None or getattr(m2.daloader,"da",None) is None:
    m2 = dh.configure_da(m2 if m2 is not None else mtk)
dl = m2.daloader
print(f"DA: {type(dl.da).__name__}\n", flush=True)

SUPER = 0x3A800000
# 采样: 每 64MB 读 4KB 头部, 判断该区域是否非零
STEP = 0x4000000      # 64MB
CHUNK = 0x1000        # 4KB
TOTAL = 0x180000000   # 6GB
print(f"扫描 super: {hex(SUPER)} 起 {hex(TOTAL)}, 每 {hex(STEP)} 采样 {hex(CHUNK)}\n", flush=True)
print(f"{'相对偏移':>12}{'绝对偏移':>14}{'非零字节':>10}", flush=True)
print("-"*40, flush=True)
out = DUMP/"scan_chunk.bin"
nonzero_ranges = []
for off in range(0, TOTAL, STEP):
    try:
        dl.readflash(addr=SUPER+off, length=CHUNK, filename=str(out), parttype=None, display=False)
        d = out.read_bytes() if out.exists() else b""
        nz = sum(1 for b in d if b != 0)
    except Exception as e:
        print(f"  {hex(off):>12} 异常 {e}", flush=True); continue
    mark = ""
    if nz:
        nonzero_ranges.append((off, nz))
        mark = "  <== 有数据"
    print(f"{hex(off):>12}{hex(SUPER+off):>14}{nz:>10}{mark}", flush=True)

print(f"\n=== 有数据的采样点: {len(nonzero_ranges)} / {TOTAL//STEP} ===", flush=True)
for off, nz in nonzero_ranges:
    print(f"  {hex(off)}: {nz} 字节非零", flush=True)
if nonzero_ranges:
    lo = nonzero_ranges[0][0]; hi = nonzero_ranges[-1][0]
    print(f"\n  数据大致范围: {hex(lo)} .. {hex(hi)}  (约 {(hi-lo)/1024**3:.2f} GB)", flush=True)
