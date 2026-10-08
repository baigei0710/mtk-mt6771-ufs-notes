#!/usr/bin/env python3.14
"""回退 boot_a/vbmeta_a + 读取 expdb 崩溃日志"""
import os
import hashlib, sys
from pathlib import Path
MTKCLIENT=Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient"))); DUMP=Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP=DUMP/"backup"
(DUMP/"logs").mkdir(exist_ok=True); (DUMP/".state").unlink(missing_ok=True)
sys.path.insert(0,str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk
from mtkclient.config.mtk_config import MtkConfig
from mtkclient.Library.Partitions.gpt import GptSettings
from mtkclient.Library.DA.mtk_da_handler import DaHandler

print("="*66, flush=True)
print(" 回退 boot_a + vbmeta_a, 并读取 expdb 崩溃日志", flush=True)
print("="*66, flush=True)

blobs={}
for n in ("boot_a","vbmeta_a"):
    f=BACKUP/f"{n}.bin"
    if not f.exists(): print(f"  ✗ 缺备份 {f}", flush=True); sys.exit(1)
    blobs[n]=f.read_bytes()
    print(f"  {n}.bin {len(blobs[n])} sha256={hashlib.sha256(blobs[n]).hexdigest()[:16]}", flush=True)

print("\n★ 请给平板上电 / 重新插拔 USB ★", flush=True)
cfg=MtkConfig(loglevel=20,gui=False); cfg.gpt_settings=GptSettings(0,0,0)
mtk=Mtk(config=cfg,loglevel=20)
if not mtk.preloader.init(display=True):
    print("\n!! 握手失败 — 试: 长按电源15秒 -> 拔线 -> 不按键插回", flush=True); sys.exit(1)
print("\n★ 握手成功 ★", flush=True)
dh=DaHandler(mtk,20); m2=dh.connect(mtk)
if m2 is None or getattr(m2.daloader,"da",None) is None:
    m2=dh.configure_da(m2 if m2 is not None else mtk)
if m2 is None or getattr(m2.daloader,"da",None) is None:
    print("!! DA 装载失败", flush=True); sys.exit(1)
dl=m2.daloader

pmap={}
for p in dl.get_partition_data(parttype=None):
    n=getattr(p,"name","")
    if n: pmap[n]=(p.sector*cfg.pagesize, p.sectors*cfg.pagesize)

# --- 1) 回退 ---
ok=True
for n in ("boot_a","vbmeta_a"):
    if n not in pmap: print(f"  ✗ 找不到分区 {n}", flush=True); ok=False; continue
    off,sz=pmap[n]
    print(f"\n>>> 恢复 {n} @ 0x{off:x} ({len(blobs[n])} 字节) ...", flush=True)
    r=dl.writeflash(addr=off,length=len(blobs[n]),filename="",wdata=blobs[n],display=True)
    print(f"    结果: {r}", flush=True)
    if not r: ok=False

# --- 2) 读 expdb ---
print(f"\n=== 读取 expdb (崩溃日志) ===", flush=True)
if "expdb" in pmap:
    off,sz=pmap["expdb"]
    out=DUMP/"expdb.bin"
    print(f"  expdb @ 0x{off:x} 大小 {sz} ({sz/1024/1024:.1f} MB)", flush=True)
    dl.readflash(addr=off,length=min(sz,0x1400000),filename=str(out),parttype=None,display=False)
    if out.exists():
        d=out.read_bytes()
        nz=sum(1 for b in d if b!=0)
        print(f"  已读 {len(d)} 字节, 非零 {nz}", flush=True)
        # 提取可读文本
        import re
        txt=[]
        for m in re.finditer(rb'[\x20-\x7e\n\r\t]{20,}', d):
            txt.append(m.group().decode('utf-8','replace'))
        if txt:
            Path(DUMP/"expdb_strings.txt").write_text("\n\n=== --- ===\n\n".join(txt), encoding='utf-8')
            print(f"  提取到 {len(txt)} 段文本 -> expdb_strings.txt", flush=True)
            print(f"\n  --- 最后 3 段 (最新日志) ---", flush=True)
            for t in txt[-3:]:
                print("  " + t[-1500:].replace("\n","\n  "), flush=True)
        else:
            print("  无可读文本", flush=True)
else:
    print("  ✗ 分区表里没有 expdb", flush=True)

print("\n"+"="*66, flush=True)
print(" ✓ 回退完成" if ok else " ⚠️ 回退部分失败", flush=True)
print("="*66, flush=True)
