#!/usr/bin/env python3.14
"""
写入 BCB (Bootloader Control Block) 让设备下次启动进入 Recovery

原理: Android bootloader 会读 misc/para 分区偏移 0x00 的 command 字段,
      内容为 "boot-recovery" 时进入 recovery 模式.
      本机 para 分区的 0x0000-0x0800 目前是全零 (未使用), 可直接利用.

安全措施:
  - 先完整读回当前 para 并保存 (基线对比)
  - 只写 0x00-0x800 范围的 command 字段, 不动其他数据
  - 回退: 把备份的 para.bin 原样写回

用法: perl -e 'alarm 600; exec @ARGV' /opt/homebrew/bin/python3.14 recovery_boot.py [boot-recovery|bootonce-bootloader|clear]
"""
import os
import hashlib
import struct
import sys
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))
BACKUP = DUMP / "backup"

PARA_PART = "para"
FALLBACK_OFF = 0x108000          # 来自 GPT: para @ 0x108000
FALLBACK_LEN = 0x80000           # 长度 512KB

(DUMP / "logs").mkdir(exist_ok=True)
(DUMP / ".state").unlink(missing_ok=True)

sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk                   # noqa: E402
from mtkclient.config.mtk_config import MtkConfig             # noqa: E402
from mtkclient.Library.Partitions.gpt import GptSettings      # noqa: E402
from mtkclient.Library.DA.mtk_da_handler import DaHandler     # noqa: E402

CMDS = {
    "boot-recovery": b"boot-recovery\x00" + b"\x00" * 18,      # 进 recovery
    "bootonce-bootloader": b"bootonce-bootloader\x00" + b"\x00" * 11,
    "clear": b"\x00" * 32,                                     # 清空 (恢复默认)
}


def build_bcb(command: bytes) -> bytes:
    """构造前 0x800 的 BCB 区 (command[32] + flags[32] + recovery[768])"""
    bcb = bytearray(0x800)
    if len(command) > 32:
        raise ValueError("command 太长")
    bcb[0:len(command)] = command
    return bytes(bcb)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "boot-recovery"
    if mode not in CMDS:
        print(f"未知模式: {mode}")
        print(f"可选: {list(CMDS)}")
        return 1

    print("=" * 66, flush=True)
    print(f" 写入 BCB -> 让设备下次启动进入 Recovery", flush=True)
    print(f" 命令: {mode}", flush=True)
    print("=" * 66, flush=True)

    # 备份文件必须存在 (回退依据)
    bak = BACKUP / "para.bin"
    if not bak.exists():
        print(f"  ✗ 缺少回退备份 {bak}", flush=True)
        return 1
    bakdata = bak.read_bytes()
    print(f"  回退备份: para.bin {len(bakdata)} 字节 "
          f"sha256={hashlib.sha256(bakdata).hexdigest()[:16]}", flush=True)

    print("\n★ 请给平板上电 / 重新插拔 USB ★", flush=True)
    cfg = MtkConfig(loglevel=20, gui=False)
    cfg.gpt_settings = GptSettings(0, 0, 0)
    mtk = Mtk(config=cfg, loglevel=20)
    if not mtk.preloader.init(display=True):
        print("\n!! 握手失败", flush=True)
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

    # 取 para 的真实偏移
    off, ln = FALLBACK_OFF, FALLBACK_LEN
    try:
        for p in dl.get_partition_data(parttype=None):
            if getattr(p, "name", "") == PARA_PART:
                off = p.sector * cfg.pagesize
                ln = p.sectors * cfg.pagesize
                break
    except Exception as e:
        print(f"  取分区表失败 ({e}), 用备用偏移", flush=True)
    print(f"  {PARA_PART} @ 0x{off:x}  长度 {ln} ({ln//1024} KB)", flush=True)

    # ---- 步骤1: 读回当前 para 内容 (只读, 用于对比) ----
    cur = DUMP / "para_current.bin"
    print(f"\n>>> 读取当前 {PARA_PART} ...", flush=True)
    dl.readflash(addr=off, length=ln, filename=str(cur), parttype=None, display=False)
    if not cur.exists():
        print("  !! 读取失败", flush=True)
        return 1
    cdata = cur.read_bytes()
    nz = sum(1 for b in cdata if b != 0)
    print(f"  读回 {len(cdata)} 字节, 非零 {nz}", flush=True)
    print(f"  与备份对比: {'完全一致' if cdata == bakdata else '有差异!'}", flush=True)
    print(f"  当前 0x00-0x40: {cdata[:0x40].hex()}", flush=True)

    # ---- 步骤2: 构造并写入 ----
    newbcb = build_bcb(CMDS[mode])
    newdata = bytearray(cdata)
    newdata[0:0x800] = newbcb
    newdata = bytes(newdata)
    diff = [i for i in range(min(len(cdata), len(newdata))) if cdata[i] != newdata[i]]
    print(f"\n  将修改 {len(diff)} 个字节, 范围 0x{min(diff):x}-0x{max(diff):x}"
          if diff else "\n  无变化")
    print(f"  新 0x00-0x40: {newdata[:0x40].hex()}", flush=True)

    print(f"\n>>> 写入 {PARA_PART} ...", flush=True)
    r = dl.writeflash(addr=off, length=len(newdata), filename="", wdata=newdata,
                      display=True)
    print(f"    结果: {r}", flush=True)
    if not r:
        print("!! 写入失败", flush=True)
        return 1

    # ---- 步骤3: 读回验证 ----
    ver = DUMP / "para_verify.bin"
    dl.readflash(addr=off, length=ln, filename=str(ver), parttype=None, display=False)
    if ver.exists():
        v = ver.read_bytes()
        ok = v[:0x800] == newbcb
        print(f"\n  写入验证: {'✓ BCB 已正确写入' if ok else '✗ 校验不符'}", flush=True)

    print("\n" + "=" * 66, flush=True)
    print(f" ✓ 完成 (命令: {mode})", flush=True)
    print("=" * 66, flush=True)
    print("\n下一步:", flush=True)
    print("  1. 断开 USB", flush=True)
    print("  2. 长按电源键开机", flush=True)
    print("  3. 观察是否进入 Android Recovery 界面", flush=True)
    print("\n回退 (若异常):", flush=True)
    print(f"  把备份的 para.bin 写回 @ 0x{off:x}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
