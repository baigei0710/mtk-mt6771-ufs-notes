#!/usr/bin/env python3.14
"""
读取并解析 super 分区的 LP metadata (动态分区表)

目的: 找出 super 内部 system 逻辑分区的真实字节偏移, 以便用 DA 直写 GSI.
原理: super 头部是 LP_METADATA_GEOMETRY, 之后是 metadata 副本,
      metadata 里含 LP_METADATA_HEADER 与分区表(LP_PARTITION_ENTRY), 每项 52 字节.

用法: perl -e 'alarm 300; exec @ARGV' /opt/homebrew/bin/python3.14 read_super.py
"""
import os
import struct
import sys
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
DUMP = Path(os.environ.get("WORK_DIR", str(Path.home()/"tablet_dump")))

SUPER_OFFSET = 0x3A800000
READ_LEN = 0x20000          # 128KB, 足够覆盖 geometry + 主 metadata

GEOM_MAGIC = b"LP_METADATA_GEOMETRY"
HEAD_MAGIC = b"LP_METADATA_HEADER"

(DUMP / "logs").mkdir(exist_ok=True)
(DUMP / ".state").unlink(missing_ok=True)

sys.path.insert(0, str(MTKCLIENT))
from mtkclient.Library.mtk_class import Mtk                   # noqa: E402
from mtkclient.config.mtk_config import MtkConfig             # noqa: E402
from mtkclient.Library.Partitions.gpt import GptSettings      # noqa: E402
from mtkclient.Library.DA.mtk_da_handler import DaHandler     # noqa: E402


def s64(v):
    """把无符号 64 位还原成有符号 (LP 用补码表示负值)"""
    return v - (1 << 64) if v >= (1 << 63) else v


def parse_entries(buf, md_rel, super_off):
    """md_rel = metadata 在 buf 内的偏移; 解析其分区表"""
    hi = buf.find(HEAD_MAGIC, md_rel)
    if hi == -1:
        print(f"    未找到 {HEAD_MAGIC.decode()}", flush=True)
        return
    # LP 规范: 魔数占 20 字节 (18 字节字符串 + 2 字节填充), 字段从 +20 开始
    body = hi + 20
    # 校验 major/minor 是否合理, 不合法则说明布局不符
    h_major, h_minor = struct.unpack_from("<HH", buf, body)
    if not (1 <= h_major <= 100 and 0 <= h_minor <= 100):
        print(f"    魔数长度疑似不符 (major={h_major} minor={h_minor})", flush=True)
        return
    (_ph_size, hdr_size, _hcrc, tables_size, _tcrc,
     max_sectors) = struct.unpack_from("<IIIIII", buf, body + 4)
    print(f"    头部: ver={h_major}.{h_minor} hdr_size={hdr_size} "
          f"tables_size={tables_size} max_sectors={max_sectors}", flush=True)
    t = hi + hdr_size
    n = tables_size // 52
    print(f"    分区条目: {n} 个\n", flush=True)
    print(f"    {'名称':<20}{'起始扇区':>12}{'扇区数':>12}{'super内偏移':>15}{'绝对偏移':>15}{'大小':>10}", flush=True)
    print("    " + "-" * 86, flush=True)
    found = {}
    for i in range(n):
        e = t + i * 52
        if e + 52 > len(buf):
            break
        name = buf[e:e + 36].split(b"\x00")[0].decode(errors="replace")
        if not name:
            continue
        # LP 分区条目布局 (52 字节): name[36] + attributes(u32) + first_sector(u64) + num_sectors(u32)
        _attrs, first, count = struct.unpack_from("<IQI", buf, e + 36)
        first = s64(first)
        count = s64(count)
        rel = first * 512
        if rel < 0 or count <= 0:
            continue
        found[name] = (rel, count * 512)
        mark = " ★" if name == "system" else ""
        print(f"    {name:<20}{first:>12}{count:>12}{hex(rel):>15}"
              f"{hex(super_off + rel):>15}{count * 512 / 1024**3:>9.2f}G{mark}", flush=True)
    return found


def main():
    print("=" * 66, flush=True)
    print(" ★ 读取 super 的 LP metadata —— 请给平板上电 / 重新插拔 USB ★", flush=True)
    print("=" * 66, flush=True)

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

    out = DUMP / "super_head.bin"
    print(f"\n  读取 super @ {hex(SUPER_OFFSET)}, {READ_LEN // 1024}KB ...", flush=True)
    try:
        dl.readflash(addr=SUPER_OFFSET, length=READ_LEN, filename=str(out),
                     parttype=None, display=False)
    except Exception as e:
        print(f"  读取异常: {e}", flush=True)
        return 1
    if not out.exists():
        print("  !! 未生成文件", flush=True)
        return 1

    d = out.read_bytes()
    print(f"  已读 {len(d)} 字节", flush=True)

    gi = d.find(GEOM_MAGIC)
    print(f"\n=== {GEOM_MAGIC.decode()} 位置: "
          f"{hex(gi) if gi != -1 else '未找到'} ===", flush=True)
    if gi == -1:
        print(f"  头部 64 字节: {d[:64].hex()}", flush=True)
        print("  -> 不是 LP 布局; GSI 可能可直接写到 super 起始偏移", flush=True)
        return 0

    p = gi + len(GEOM_MAGIC)
    g_major, g_minor, g_hdr = struct.unpack_from("<III", d, p)
    p += 12
    n_prim, es_prim = struct.unpack_from("<II", d, p)
    p += 8
    n_back, es_back = struct.unpack_from("<II", d, p)
    p += 8
    n_geo, es_geo = struct.unpack_from("<II", d, p)
    p += 8
    print(f"  major={g_major} minor={g_minor} header_size={g_hdr}", flush=True)
    print(f"  主表 {n_prim} 项 x {es_prim}B / 备份表 {n_back} 项 x {es_back}B", flush=True)
    print(f"  geometry 表 {n_geo} 项 x {es_geo}B", flush=True)

    geo_start = p + 4          # geometry 表紧跟其后
    print(f"\n=== geometry 表 (metadata 副本位置) ===", flush=True)
    any_found = False
    for i in range(n_geo):
        e = geo_start + i * 12
        if e + 12 > len(d):
            break
        nsec, phys, offb = struct.unpack_from("<IQQ", d, e)
        phys = s64(phys)
        offb = s64(offb)
        print(f"  副本{i}: sectors={nsec} phys=0x{phys:x} offset_b=0x{offb:x}", flush=True)
        if 0 <= phys < len(d) - 80:
            print(f"    -> metadata 在 super 内偏移 0x{phys:x}, 解析:", flush=True)
            found = parse_entries(d, phys, SUPER_OFFSET)
            any_found = True
            if found and "system" in found:
                rel, size = found["system"]
                print(f"\n  ★★ system 逻辑分区:", flush=True)
                print(f"      super 内偏移 : 0x{rel:x}", flush=True)
                print(f"      绝对 flash 偏移: 0x{SUPER_OFFSET + rel:x}", flush=True)
                print(f"      分区大小     : {size} 字节 ({size / 1024**3:.2f} GB)", flush=True)
            break
    if not any_found:
        print("  geometry 表未指向本缓冲内的 metadata, 需要读更多数据", flush=True)

    print(f"\n=== 头部前 128 字节 ===", flush=True)
    print("  " + d[:128].hex(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
