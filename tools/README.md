# 工具脚本

从这次折腾中沉淀的可复用脚本。**都用纯 Python / bash 写成，除了 mtkclient 本身不需要额外依赖**
（唯一例外：硬超时用 `perl`，macOS 自带）。

## 环境要求

```bash
# Python 必须是装了 pyusb + pycryptodome 的那个解释器
# macOS 系统 Python 通常没有，Homebrew 的才有
export PYTHON=/opt/homebrew/bin/python3.14

# mtkclient 位置（脚本会读这个环境变量）
export MTKCLIENT_DIR=$HOME/mtkclient

# 工作目录（备份、镜像、日志都放这里）
export WORK_DIR=$HOME/tablet_dump
```

**重要**：跑之前先确认解释器有依赖：
```bash
$PYTHON -c "import usb, Crypto; print('OK')"
```

## ⚠️ 硬超时（必读）

Python 的 `signal.alarm` **无法中断阻塞在 C 层的 USB 调用**。
所有脚本都要用 perl 外挂硬超时，否则会把终端挂死：

```bash
perl -e 'alarm 300; exec @ARGV' $PYTHON 脚本.py
```

---

## 脚本清单

### 抓取 / 备份

| 脚本 | 用途 |
|---|---|
| `grab.py` + `grab.sh` | 抓取关键分区（IMEI / 射频校准 / 引导链） |
| `grab_more.py` + `grab_more.sh` | 补充抓取（`para`、基带、preloader） |

**共同点**：先进入等待握手循环，**你再去插线**。
因为 preloader 的 USB 窗口只有 **0.3 秒**（`Port.py:75`），必须先等再插。

```bash
$WORK_DIR/grab.sh 300        # 等待 300 秒
```

### 分析

| 脚本 | 用途 |
|---|---|
| `verify_addr.py` | 交叉验证分区偏移（用分区表算 vs 手工值） |
| `scan_super.py` | 扫描 `super` 分区，看哪块区域有数据 |
| `read_super.py` | 解析 `super` 的 LP metadata（**标准格式；MTK 私有格式解析不出来**） |

### 修改 boot 镜像

| 脚本 | 用途 |
|---|---|
| `bootpatch.py` | 解包 boot 镜像 → 改 ramdisk 属性 → 重打包 |

**关键实现点**（都是踩过坑的）：
- boot header **整块复制**，只改 `ramdisk_size`（`0x10` 处）
- v2 header 的 `extra_cmdline` 从 **`0x608`** 开始，自己重建会误清零后面的字段
- AVB footer 必须保持在分区末尾固定位置 → 在"内容"与"tail"之间补齐
- cpio 填充计算要在写入**之后**取长度（变量遮蔽陷阱）

### 引导 / 回退

| 脚本 | 用途 |
|---|---|
| `recovery_boot.py` | 往 `para` 的 BCB 写 `boot-recovery` → 进 recovery |
| `para_rollback.py` | 恢复 `para`（清除 BCB） |
| `rollback.py` | 恢复 `boot_a` + `vbmeta_a` 到原始备份 |
| `rollback_and_log.py` | 回退 + 读取 `expdb` 崩溃日志（**排障利器**） |

### 刷写（**风险操作，谨慎**）

| 脚本 | 用途 |
|---|---|
| `flash_root.py` | 写 patch 过的 boot + 关校验的 vbmeta |
| `fix_recovery_usb.py` | 生成"修 recovery USB"的 boot 镜像（**实测会 bootloop**） |
| `flash_recovery_usb.py` | 刷入上面那个镜像 |

### 诊断

| 脚本 | 用途 |
|---|---|
| `monitor_usb2.sh` | 监听 USB 枚举变化 + adb 设备（recovery 下排障用） |

### 参考

| 脚本 | 用途 |
|---|---|
| `repro_ufs_parttype.py` | 复现 mtkclient 的 UFS parttype bug（隔离、无需设备） |

---

## 典型用法

### 抓关键分区
```bash
perl -e 'alarm 600; exec @ARGV' $PYTHON $WORK_DIR/grab.py 300
```

### 进 recovery 测 USB
```bash
# 1. 写 BCB
perl -e 'alarm 300; exec @ARGV' $PYTHON $WORK_DIR/recovery_boot.py

# 2. 拔线让设备启动（DA 会把设备保持在下载模式，必须断开才会继续启动）
# 3. 进 recovery 后插线
# 4. 监听
$WORK_DIR/monitor_usb2.sh 120
```

### 出事时回退
```bash
# boot + vbmeta
perl -e 'alarm 600; exec @ARGV' $PYTHON $WORK_DIR/rollback.py

# 连 expdb 日志一起读回来（推荐，能看到为什么失败）
perl -e 'alarm 600; exec @ARGV' $PYTHON $WORK_DIR/rollback_and_log.py
```

---

## 自己写脚本时要注意

**绕过 mtkclient 的 CLI 直接调 API**（因为 CLI 的 `connect()` 有假失败问题）：

```python
import os, sys
from pathlib import Path

MTKCLIENT = Path(os.environ.get("MTKCLIENT_DIR", str(Path.home()/"mtkclient")))
sys.path.insert(0, str(MTKCLIENT))

from mtkclient.Library.mtk_class import Mtk
from mtkclient.config.mtk_config import MtkConfig
from mtkclient.Library.Partitions.gpt import GptSettings
from mtkclient.Library.DA.mtk_da_handler import DaHandler

cfg = MtkConfig(loglevel=20, gui=False)
cfg.gpt_settings = GptSettings(0, 0, 0)        # ★ 必须设, 默认是 None
mtk = Mtk(config=cfg, loglevel=20)

if not mtk.preloader.init(display=True):        # 会持续重试, 先调再插线
    sys.exit(1)

dh = DaHandler(mtk, 20)
m2 = dh.connect(mtk)
if m2 is None or getattr(m2.daloader, "da", None) is None:
    m2 = dh.configure_da(m2 if m2 is not None else mtk)   # ★ DA 装载

dl = m2.daloader
# 现在可以 dl.readflash(...) / dl.writeflash(...) / dl.get_partition_data(...)
```

**两处容易漏的初始化**：
1. `cfg.gpt_settings` —— 默认 `None`，CLI 靠 `ArgHandler` 填
2. `dl.da` —— 默认 `None`，需要 `configure_da()` 才会创建 `DAXFlash`
