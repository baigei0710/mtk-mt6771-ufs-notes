# UFS 启动布局：preloader 为什么在 LU1

**这是一次实测踩坑的记录** —— 一开始以为 preloader 在 LU0，反复找不到，
后来才发现 UFS 的结构和 eMMC 完全不同。

---

## eMMC vs UFS：preloader 的存放位置

| | eMMC | UFS |
|---|---|---|
| preloader 位置 | 硬件分区 `boot0` / `boot1` | **专用 boot LUN（LU1）** |
| 常见访问名 | `/dev/block/mmcblk0boot0` | LUN1 |
| 分区表（GPT） | 在 `user` 区 | 在 **LU0** |
| 是否有 boot0/boot1 概念 | ✅ 有 | ❌ **没有** |

**关键差异**：UFS 没有 `boot0`/`boot1` 这种独立的硬件分区概念，
它的"启动区"是**独立的逻辑单元（LU）**。

---

## 本机的实测布局

> 存储芯片：Micron UFS 2.2（型号 `MT128GAXAU2U22`，128GB）
> 平台：MT6771 / Helio P60（`hwcode 0x788`）

| LUN | 大小 | 内容 |
|---|---|---|
| **LU0** | `0x1dcb000000` (119GB) | GPT + 全部分区（**但没有 preloader**） |
| **LU1** | `0x400000` (4MB) | **★ preloader 在这里** |
| **LU2** | `0x400000` (4MB) | 内容与 LU1 相同 |
| **LU3** | — | RPMB |

### LU1 里的 preloader 结构

```
偏移 0x0      : ASCII "UFS_BOOT"          ← 存储类型标记
偏移 0x1000   : MMM 魔数 (4D 4D 4D 01 38 00 00 00)
偏移 0x42eb4  : "MTK_BLOADER_INFO_v36"
```

裁出来是 **273204 字节**，内部记录的原始文件名是
`preloader_tb8788p1_64_bsp.bin` —— 与设备树代号一致。

---

## 怎么确认 preloader 不在 LU0

读了 LU0 前 8MB，搜索所有已知特征串，**全部未命中**：

```
MTK_BLOADER_INFO  -> 未找到
MMM               -> 未找到
BRLYT             -> 未找到
UFS_BOOT          -> 未找到
EMMC_BOOT         -> 未找到
```

非零字节分布（整个 8MB 只有约 4400 个非零字节）：

```
偏移 0x0      : 4324 个非零    ← GPT 保护 MBR + GPT 头 + 分区表
偏移 0x100000 :   12 个非零
偏移 0x110000 :    5 个非零
偏移 0x120000 :   31 个非零
偏移 0x140000 :   30 个非零
```

**结论：LU0 开头只有 GPT 结构，没有 preloader。**

---

## 怎么读 LU1（以及遇到的 bug）

### 正常写法
```bash
# lu1 映射到 UFSPartitionType.BOOT1 (LUN1)
mtk.py r <某分区> out.bin --parttype=lu1
```

### 但会被 mtkclient 的 bug 挡住

`storage.py:319-321` 的变量遮蔽导致 `lu0`~`lu3` **全部不可达**。
详见 [`mtkclient-bugs.md`](mtkclient-bugs.md) 第 1 节。

### 临时绕过方式
用 `--parttype=boot1`（它也映射到 LUN1）：

```bash
mtk.py r <某分区> out.bin --parttype=boot1
```

**注意**：`boot1` 是 eMMC 语义的命名，对 UFS 用户完全不可发现，
而且没有任何提示说明它等于 "LUN1"。这也是为什么要提 PR 修它。

---

## UFS LUN 的完整映射（mtkclient 内部）

`storage.py:287-343`：

| CLI 参数 | 内部映射 | 实际 LUN |
|---|---|---|
| `user` | `UFSPartitionType.USER` | LUN0 |
| `lu0` | `UFS-LUA0` | LUN0 |
| `lu1` / `boot1` | `UFSPartitionType.BOOT1` | LUN1 |
| `lu2` / `boot2` | `UFSPartitionType.BOOT2` | LUN2 |
| `lu3` / `rpmb` | `UFSPartitionType.RPMB` | LUN3 |

枚举定义（`storage.py:44`）：
```python
class UFSPartitionType:
    BOOT1 = 1     # LU1
    BOOT2 = 2     # LU2
    USER  = 3     # LU0
    RPMB  = 4     # LU3
```

**注意顺序很反直觉**：`USER`（主数据区）的值是 `3`，而 `BOOT1` 是 `1`。

---

## 为什么 LU1/LU2 都是 4MB

这是 UFS 标准里 boot LUN 的**典型尺寸**。

**这个细节是定位 preloader 的关键线索** —— 当在 LU0 里找不到 preloader，
而 LU1/LU2 各正好 4MB 时，就应该想到"这是 boot LUN"。

---

## 顺带：UFS 的 GPT 在哪

**在 LU0**。本机的实际布局：

```
LU0 偏移 0x0        : GPT 保护 MBR（0x1FE 处是 55 AA）
LU0 偏移 0x1000     : GPT 头（LBA1，块大小 0x1000）
LU0 偏移 0x8000     : 第一个分区 boot_para
...
LU0 偏移 0x3a800000 : super（动态分区容器，6GB）
LU0 偏移 0x1ba800000: userdata
```

**块大小是 0x1000 (4096)**，不是 eMMC 常见的 512。
`config.pagesize` 在 mtkclient 里仍是 512（用于扇区换算），
但 UFS 的 `block_size` 是 `0x1000`。

---

## 教训

1. **UFS 和 eMMC 的启动结构完全不同**，不能套用 eMMC 的经验
2. **在 LU0 找不到 preloader 是正常的** —— 它就该在 LU1
3. **LU1/LU2 各 4MB 是重要线索**
4. UFS 的 preloader **公开渠道几乎找不到**（因为各机型的 DRAM/PMIC 参数不同，
   通用性为零）→ **只能从自己的设备里取**
