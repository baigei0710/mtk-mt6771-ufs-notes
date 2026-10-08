# 设备档案（已脱敏）

> ⚠️ **设备唯一标识已脱敏**（序列号、ME_ID、SOC_ID、UFS CID）。
> 这些是设备独有的标识，公开仓库不应包含。

---

## 基本信息

| 项目 | 值 |
|---|---|
| SoC | MT6771 / Helio P60（`hwcode 0x788`） |
| 设备类型 | 定制学习机（平板） |
| 型号 | `PF135` |
| 设备树代号 | **`tb8788p1_64_bsp`** |
| 序列号 | `Q204PSfSg*******`（已脱敏） |

---

## 系统身份

### 原厂 build fingerprint（Android 11）
```
alps/full_tb8788p1_64_bsp/tb8788p1_64_bsp:11/RP1A.200720.011/1736916084:user/release
```
| 项目 | 值 |
|---|---|
| Android 版本 | **11**（build ID `RP1A.200720.011`） |
| 构建时间 | Unix `1736916084` → 2025-01-15 |
| 构建类型 | `user`（正式版，非 userdebug） |

### recovery 的 fingerprint（Android 10，注意版本不同！）
```
alps/full_tb8788p1_64_bsp/tb8788p1_64_bsp:10/QP1A.190711/1716949289 user/release-keys
```
→ **boot 分区里的 ramdisk 是 Android 10 的 recovery**，
而正常启动的系统是 Android 11。

### 关键属性
```
ro.build.type        = user
ro.board.platform    = mt6771
ro.boot.dynamic_partitions = true
ro.hardware          = mt6771
ro.product.model     = PF135
ro.product.device    = Q20
ro.secure            = 1
ro.debuggable        = 0
ro.adb.secure        = 1
persist.sys.usb.config = none
```

---

## 内核命令行（从 expdb 日志提取）

```
console=tty0 console=ttyS0,921600n1
vmalloc=400M slub_debug=OFZPU swiotlb=noforce page_owner=on
androidboot.hardware=mt6771
firmware_class.path=/vendor/firmware
androidboot.boot_devices=bootdevice,soc/11230000.mmc,11230000.mmc,soc/11270000.ufshci
ramoops.mem_address=0x54410000 ramoops.mem_size=0xe0000
ramoops.pmsg_size=0x10000 ramoops.console_size=0x40000
bootopt=64S3,32N2,64N2 buildvariant=user
root=/dev/ram
androidboot.vbmeta.avb_version=1.1
androidboot.vbmeta.device_state=locked          ← 注意标称 locked
androidboot.veritymode=enforcing
androidboot.slot_suffix=_a   androidboot.slot=a
androidboot.verifiedbootstate=green
androidboot.serialno=<已脱敏>
androidboot.bootreason=PowerKey
gpt=1 usb2jtag_mode=0
```

---

## 安全状态

### DA 侧读取的 target config
```
Target config: 0x0
  SBC enabled: False      ← Secure Boot Config
  SLA enabled: False      ← Serial Link Auth
  DAA enabled: False      ← Download Agent Auth
  Mem read auth: False
  Mem write auth: False
  Cmd 0xC8 blocked: False
```
**→ 无签名校验强制。**

### seccfg 分区
```
魔数: "MMMM"    版本: v4
4d4d4d4d 04000000 3c000000 01000000 00000000 00000000 45454545 dd5bdde5
"MMMM"   v4       size=60  ...
```
preloader 日志：`[LIB] SEC CFG 'v4' exists` / `[LIB] SEC CFG is valid. Lock state is 1`

### userdata 加密
从 fstab：
```
fileencryption=aes-256-xts:aes-256-cts:v2
keydirectory=/metadata/vold/metadata_encryption
inlinecrypt
```

---

## 存储

| 项目 | 值 |
|---|---|
| 类型 | **UFS 2.2** |
| 芯片型号 | Micron `MT128GAXAU2U22`（128GB） |
| 块大小 | `0x1000` (4096) |
| LU0 | `0x1dcb000000` (119GB) |
| LU1 / LU2 | `0x400000` (4MB each) |
| LU3 | RPMB |
| CID | `<已脱敏>` |

---

## 分区布局（关键项）

布局是 **Android 11 A/B + 动态分区**，共 49~53 个 GPT 分区。

| 分区 | 偏移 | 长度 |
|---|---|---|
| `boot_para` | `0x8000` | `0x100000` |
| `para` (= misc) | `0x108000` | `0x80000` (512KB) |
| `expdb` | `0x188000` | `0x1400000` (20MB) |
| `seccfg` | `0xc000000` | `0x800000` |
| `vbmeta_a` | `0x14500000` | `0x800000` |
| `lk_a` | `0x20900000` | `0x100000` |
| `boot_a` | `0x20a00000` | `0x2000000` (32MB) |
| **`super`** | `0x3a800000` | `0x180000000` (6GB) |
| `userdata` | `0x1ba800000` | `0x1c0ccf8000` (~112GB) |

**总容量**：`0x1dcb019000`（约 119GB）

### `super` 内部（虚拟 A/B）
能读出的逻辑分区名：
```
system_a   vendor_a   product_a
system_b   vendor_b   product_b
system_a-cow  vendor_a-cow  product_a-cow     ← COW 分区 = 虚拟 A/B 特征
super
```
**但元数据用了 MTK 私有格式**（无标准 `LP_METADATA_GEOMETRY` 魔数），
找不到分区尺寸和偏移字段 → 无法定位 `system_a` 的精确偏移。

---

## USB 控制器

从设备树（DTB，位于 boot 镜像的 AVX tail 区）：

```
/usb3@11200000         compatible: mediatek,mt6771-mtu3   ← gadget 控制器
/usb3_xhci@11200000    ← XHCI host 控制器
/usb0phy@11f40000      ← USB PHY
/usb_c_pinctrl
/ssusb_ip_sleep
```

### 关键细节
- **UDC 名是 `musb-hdrc`**（不是 `mtu3`）—— 内核日志：`mtu3_phone musb-hdrc`
- `cmode` 属性实际路径：`/sys/devices/platform/11201000.usb3/cmode`
- `cmode` 取值：**`0` = NORMAL，`2` = HOST_ONLY**
- 驱动源码路径：`drivers/usb/mtu3/mt6771/`

---

## 硬件清单（从内核日志提取）

| 部件 | 型号/驱动 |
|---|---|
| 屏幕（LCM） | `g10_zj_hra_hx83102e_boe_wuxganl_ips_101`（BOE 面板 + HX83102E 驱动） |
| 触摸 | Himax（`Himax_firmware.bin`） |
| 音频功放 | `aw87xxx` |
| PMIC | **MT6358** + **MT6370**（副 PMIC） |
| 安全芯片 | TKCore（`persist` 里有 `tkcore_protect_data_file` SELinux 上下文） |
| 存储 | UFS（`11270000.ufshci`） |

---

## boot 镜像结构

```
magic          : "ANDROID!"   (boot v2)
page_size      : 2048
kernel_size    : 9671351      (gzip 压缩, 解压后 ~25.9MB)
ramdisk_size   : 9226204      (gzip 压缩的 cpio)
kernel_addr    : 0x40080000
ramdisk_addr   : 0x55000000
cmdline        : bootopt=64S3,32N2,64N2 buildvariant=user
layout         :
  header  : 0x0        (2048)
  kernel  : 0x800      (9671351)
  ramdisk : 0x93a000   (9226204)
  AVB0    : 0x1222000  ← 镜像内的 AVB 元数据
  AVBf    : 0x1ffffc0  ← AVB footer（分区末尾）
  总计    : 0x2000000  (正好 32MB 分区)
DTB            : 0x1206840 (在 AVB 区内)
```

### ramdisk 内容结构
```
first_stage_ramdisk/avb/q-gsi.avbpubkey     ← ★ 原生支持 GSI（有 q-gsi 公钥）
first_stage_ramdisk/avb/r-gsi.avbpubkey
first_stage_ramdisk/avb/s-gsi.avbpubkey
first_stage_ramdisk/fstab.mt6771
first_stage_ramdisk/system/bin/e2fsck
system/etc/init/hw/init.rc
init.recovery.mt6771.rc
init.recovery.mt8788.rc
prop.default                                 (15606 字节)
sepolicy / plat_file_contexts / ...
共 444 个 cpio 条目
```

**重要**：`init.rc` 第一行是 `import /init.recovery.${ro.hardware}.rc`
→ **`init.recovery.mt6771.rc` 在正常启动时也会被加载**（不只是 recovery）。

---

## fstab 关键项

```
system  /system  ext4  ro ... avb=vbmeta_system,logical,first_stage_mount,avb_keys=/avb/q-gsi.avbpubkey
vendor  /vendor  ext4  ro ... avb,logical,first_stage_mount
product /product ext4  ro ... avb,logical,first_stage_mount
```
**`avb_keys=/avb/q-gsi.avbpubkey`** → 这台设备**原生允许 GSI**。

---

## 管控情况

设备是定制学习机，管控策略非常精准地**堵住了所有执行通道**：

| 入口 | 状态 |
|---|---|
| 设置 | 🔒 锁死（无法开 USB 调试） |
| 浏览器 | ⚠️ 存在但**禁止输入网址** |
| adb | ❌ 从未启用 |
| fastboot | ❌ bootloader 不实现 |
| MTP | ❌ 无接口 |
| recovery USB | ❌ 被 `cmode 2` (HOST_ONLY) 废掉 |

**但 flash 完全可读写**（DA 硬件级入口堵不住）。
