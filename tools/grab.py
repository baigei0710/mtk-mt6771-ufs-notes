#!/usr/bin/env python3.14
"""
MT6771 UFS 平板 - 握手 + 关键数据抢救 (纯读取, 零风险)

用法: python3.14 grab.py [握手超时秒数]
      (外部请用 perl -e 'alarm N; exec @ARGV' 再包一层, 因为阻塞式USB调用
       无法被 Python 的 SIGALRM 中断)

流程: 先进入等待 -> 你插USB -> 握手成功 -> 同一 DA 会话内读完数据
"""
import os
import sys
import time
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP = DUMP / "backup"

# 抢救清单: 只抓"只存在于这台机器、全世界下载不到"的数据
CRITICAL = ["proinfo", "persist", "nvram", "nvdata", "nvcfg", "seccfg", "frp"]
EXTRA = ["lk_a", "tee_a", "gz_a", "vbmeta_a", "boot_a", "dtbo_a"]

deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 120)

BACKUP.mkdir(parents=True, exist_ok=True)
(DUMP / "logs").mkdir(exist_ok=True)
for f in (DUMP / ".state", BACKUP / ".state"):
    f.unlink(missing_ok=True)

sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk          # noqa: E402
from mtkclient.config.mtk_config import MtkConfig    # noqa: E402

print("=" * 62, flush=True)
print(" ★ 现在请给平板上电 / 重新插拔 USB ★", flush=True)
print("   不按任何键 = preloader 模式", flush=True)
print(f"   等待上限 {int(deadline - time.time())} 秒", flush=True)
print("=" * 62, flush=True)

cfg = MtkConfig(loglevel=20, gui=False)

# 必须设置: MtkConfig.gpt_settings 默认是 None (mtk_config.py:65),
# CLI 靠 ArgHandler 填 GptSettings(0,0,0); 绕过 CLI 就得自己填.
# 0 = 让 mtkclient 从设备 GPT 头自动探测条目数/条目大小/起始 LBA
from mtkclient.Library.Partitions.gpt import GptSettings   # noqa: E402
cfg.gpt_settings = GptSettings(0, 0, 0)

mtk = Mtk(config=cfg, loglevel=20)

# preloader.init() 自身就是"等待并重试"的循环
if not mtk.preloader.init(display=True):
    print("\n!! 握手失败 (超时内未捕获设备)", flush=True)
    sys.exit(1)

print(f"\n★ 握手成功 ★ is_brom={cfg.is_brom} "
      f"hwcode={hex(cfg.hwcode) if cfg.hwcode else '?'}", flush=True)
print(">> 设备已连上, 请勿拔线. 开始装 DA <<\n", flush=True)

# 关键: 必须让 DA 装载完成, 否则 daloader.da 为 None (上次就是漏了这步)
# 顺序很重要: connect() 走 .state 重连缓存路径来设置 dl.da;
#            若失败再退回 configure_da() 走完整 DA 上传流程
from mtkclient.Library.DA.mtk_da_handler import DaHandler   # noqa: E402

dh = DaHandler(mtk, 20)

print("  connect() ...", flush=True)
try:
    mtk2 = dh.connect(mtk)
except Exception as e:
    print(f"    connect 异常: {e}", flush=True)
    mtk2 = None

if mtk2 is None or getattr(mtk2.daloader, "da", None) is None:
    print("  connect 未装载 DA, 改走 configure_da() 完整上传 ...", flush=True)
    if mtk2 is None:
        mtk2 = mtk
    mtk2 = dh.configure_da(mtk2)

if mtk2 is None:
    print("!! DA 装载失败", flush=True)
    sys.exit(1)

dl = mtk2.daloader
if getattr(dl, "da", None) is None:
    print("!! dl.da 仍为 None, 无法继续", flush=True)
    sys.exit(1)
print(f"  DA 装载完成, dl.da = {type(dl.da).__name__}", flush=True)
print(f"  存储类型 = {dl.daconfig.storage.flashtype}", flush=True)

# 取一次分区表 (parttype=None 即 LUN0, UFS 上不能用 lu0, 见 storage.py 的变量遮蔽 bug)
entries = dl.get_partition_data(parttype=None)
print(f"  分区表: {len(entries)} 项\n", flush=True)
print(">> 开始抢救数据 <<\n", flush=True)


def grab(names, tag):
    print(f"--- {tag} ---", flush=True)
    for name in names:
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
            size = out.stat().st_size if out.exists() else 0
            print(f" {'OK ' + str(size) if size else 'FAIL'}", flush=True)
        except Exception as e:
            print(f" 异常 {e}", flush=True)


grab(CRITICAL, "第1步: 不可再生数据 (IMEI/射频校准/传感器校准/标识)")
grab(EXTRA, "第2步: 引导链")

print("\n" + "=" * 62, flush=True)
good = []
for name in CRITICAL + EXTRA:
    f = BACKUP / f"{name}.bin"
    if f.exists() and f.stat().st_size > 0:
        good.append(name)
        print(f"  OK    {name}.bin  {f.stat().st_size} 字节", flush=True)
    else:
        print(f"  MISS  {name}.bin", flush=True)
print(f"=== {len(good)}/{len(CRITICAL + EXTRA)} 已拿到 -> {BACKUP} ===", flush=True)
print("=" * 62, flush=True)
