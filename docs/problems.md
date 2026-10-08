# 问题总览

按**问题性质**分类，而不是时间顺序 —— 这样更容易看出哪些是真实障碍、哪些是误判。

共 29 项，分五类：

| 类别 | 编号 | 性质 |
|---|---|---|
| [一、环境与工具](#一环境与工具与设备无关) | 1–5 | 与设备无关，纯粹的工具/网络问题 |
| [二、mtkclient 缺陷](#二mtkclient-自身的缺陷真实缺陷) | 6–9 | 上游工具的真实 bug |
| [三、设备机制障碍](#三设备的机制性障碍真实不是-bug) | 10–18 | MTK/Android 的设计事实，不是 bug |
| [四、我的误判](#四我的误判幽灵问题) | 19–26 | 排查了并不存在的问题 |
| [五、我造成的损失](#五我造成的实际损失3-次-bootloop) | 27–29 | 我的操作失误 |

---

## 一、环境与工具（与设备无关）

### 1. Python 环境不对
- **现象**：`/usr/bin/python3`（3.9.6）缺 `usb` / `Crypto` 模块，mtkclient 直接报错
- **真相**：macOS 系统 Python 和 Homebrew Python 是两套独立环境，只有
  `/opt/homebrew/bin/python3.14` 装了依赖
- **教训**：先确认解释器，再排查别的问题

### 2. 命令语法错误
- **现象**：`mtk.py r lu0_head 0x400000 --parttype=lu0` →
  `Failed to dump partition lu0_head as 0x400000.`
- **真相**：`r` 命令的位置参数是 `<分区名> <文件名>`，**偏移和长度必须用
  `--offset=` / `--length=`**，不能当位置参数
- **定位**：`mtk.py` 的 argparse 定义

### 3. Python 的 `signal.alarm` 无法中断阻塞式 USB 调用 ★
- **现象**：设了 `signal.alarm(90)` 硬超时，进程照样挂死不动
- **真相**：SIGALRM 的信号处理只在 Python 字节码层面生效，
  **阻塞在 C 层的 USB I/O 不可中断**
- **解法**：外层用 perl 包一层
  ```bash
  perl -e 'alarm N; exec @ARGV' python3 script.py
  ```

### 4. GitHub 被网络层阻断
- **现象**：`curl` 报 `LibreSSL: tlsv1 alert protocol version` 或
  `SSL: WRONG_VERSION_NUMBER`
- **真相**：GitHub 域名被 DNS/TLS 层拦截（其他网站正常，如 baidu / aliyun / 清华镜像）
- **解法**：开代理后恢复；国内镜像一直可用

### 5. `colima` 启动卡死
- **现象**：`colima start` 卡在 Linux VM 镜像下载，无法完成
- **原因**：VM 镜像源在当时的网络下不可达
- **教训**：**先测镜像源可达性，再启动** —— 否则会把终端挂住

---

## 二、mtkclient 自身的缺陷（真实缺陷）

### 6. UFS 的 `--parttype` 完全失效 ★★
- **位置**：`mtkclient/Library/DA/storage.py:319-321`
- **代码**：
  ```python
  else:
      if not xml:
          parttype = UFSPartitionType.USER   # 先把字符串覆盖成整数 3
      if parttype == "lu0":                  # 再拿整数 3 去比字符串 → 永远为假
  ```
- **后果**：`lu0` / `lu1` / `lu2` / `lu3` **全部不可达**
- **附带**：错误提示写的是
  `Known parttypes are "lu1","lu2","lu3","lu4"` ——
  `lu4` 根本不存在，真能用的 `lu0` 反倒没列出来
- **影响面**：只影响非 xml（XFLASH）路径，而这正是 UFS 的常见路径
- **状态**：✅ **已提 PR [#388](https://github.com/bkerler/mtkclient/pull/388)**
  详见 [`mtkclient-bugs.md`](mtkclient-bugs.md)

### 7. `mtk.py` CLI 的 `connect()` 假失败
- **现象**：所有 CLI 命令都报
  `Please disconnect, start mtkclient and reconnect.`
- **真相**：**直接调 API 却能成功握手** —— `mtk.preloader.init()` 返回 True，
  而 CLI 走的 `DaHandler.connect()` 路径返回 None
- **解法**：绕过 CLI，自己写脚本直接调 API

### 8. `dumppreloader` 会先把设备砸断
- **真相**：它第一步就调 `crasher()`，在当前状态下会往地址 0 写 0x100 字节垃圾
  并强制断开 USB
- **教训**：不要重复运行这个命令

### 9. 没有 reboot / shutdown 的 CLI 命令
- **真相**：`mtk.py da` 的子命令只有
  `peek/efuse/generatekeys/keyserver/meta/vbmeta/nvitem/patchmodem/imei/rpmb/memdump/memdram/dumpbrom/poke/seccfg`
  —— **没有 reboot 或 shutdown**
- **解法**：自己写脚本调 `daloader.shutdown(bootmode=N)`

---

## 三、设备的机制性障碍（真实，不是 bug）

### 10. preloader 的 USB 窗口只有 0.3 秒
- **源码依据**：`mtkclient/Library/Port.py:75`
  ```python
  v = ep_in(1, timeout=20)  # Do not wait 1 sec, bootloader is only active for 0.3 sec.
  ```
- **后果**：必须"**先进入等待循环，再插线**"，反过来必然错过
- **解法**：`preloader.init()` 内部会持续重试 —— 先调它，再去插线

### 11. UFS 的 preloader 不在 LU0，在 LU1
- **真相**：UFS 机型上 preloader 位于**专用 boot LUN**，不是 LU0
- **证据**：LU1 偏移 `0x0` = ASCII `UFS_BOOT`，`0x1000` = `MMM` 魔数，
  `0x42eb4` = `MTK_BLOADER_INFO_v36`
- **产物**：抠出 273204 字节的原厂 preloader
- **详见**：[`ufs-boot-layout.md`](ufs-boot-layout.md)

### 12. UFS 上 `parttype` 语义混乱
- **真相**：`lu0` == `user`，都指向 LUN0；而 `boot1` 映射到 LUN1
- **陷阱**：旧工具链里 `lu0` 表示主 LUN0，但报错文本又暗示别的含义

### 13. `super` 用 MTK 私有元数据格式
- **现象**：标准 `LP_METADATA_GEOMETRY` 魔数在 `super` 里**完全搜不到**
- **发现**：`0PLA` 出现在 `0x3000`，之后是高熵数据
- **能读到的**：分区名（`system_a` / `vendor_a` / `product_a` / `*-cow`）
- **找不到的**：分区尺寸和偏移字段
- **附带**：是 **Android 虚拟 A/B**（有 `system_a-cow` 等 COW 分区）
- **后果**：无法定位 `system_a` 的精确偏移 → 无法直写 GSI

### 14. boot 镜像的 ramdisk 是 first-stage 型
- **真相**：`prop.default` 在属性加载流程里**优先级最低**，会被
  `/system/build.prop` 覆盖
- **证据**：我们改成 `ro.debuggable=1` 后，系统里读出来仍是 0
- **结构**：
  ```
  first_stage_ramdisk/avb/q-gsi.avbpubkey     ← AVB 公钥（带 GSI 公钥）
  first_stage_ramdisk/fstab.mt6771
  prop.default                                 ← 优先级最低
  init (16 字节, 符号链接)
  ```

### 15. 系统从来就没有 adb ★
- **真相**：设置被管控锁死 → **USB 调试从没开启过** → `adbd` 根本不会启动
- **后果**：前期所有"patch boot 让 adb 可用"的思路**方向就是错的**
- **教训**：动手之前应该先确认"adb 之前是否可用"

### 16. bootloader 不支持 fastboot
- **实测**：发 `SHUTDOWN(bootmode=2)` 后屏幕全黑，`fastboot devices` 为空
- **根源**：mtkclient 的 `MtkBootModeFlag` 只有 `0:normal, 1:meta`
  —— **没有 recovery**（所以那条指令也进不了 recovery）

### 17. MTK recovery 没有标准 BCB 命令
- **源码依据**：MTK `boot_mode.c` 只用三种机制判定 recovery：
  ```c
  if(Check_RTC_Recovery_Mode())              // RTC 寄存器
      g_boot_mode = RECOVERY_BOOT;
  if(mtk_detect_key(MT65XX_RECOVERY_KEY))    // 物理按键
      g_boot_mode = RECOVERY_BOOT;
  if(unshield_recovery_detection())          // misc 中断恢复
  ```
  —— **没有读取标准 Android BCB 的 `command` 字段**
- **但**：`para` 分区偏移 `0x00` 写 `boot-recovery` **实测有效**
- **详见**：[`recovery-boot.md`](recovery-boot.md)

### 18. recovery 的 USB 完全不枚举 ★★ 最终障碍
- **现象**：进 recovery 后 Mac 侧零反应（USB 层监听证实，`ioreg` 和 `adb devices` 都为空）
- **根因**（从 `expdb` 内核日志拿到）：
  ```
  (4)[1:init]gadgets_make name=g1            ← gadget 建好了
  (4)[1:init]function_make name=ffs.adb      ← adb 功能建好了
  (4)[1:init]musb_cmode_store NORMAL --> HOST_ONLY   ← ★ 被切成主机模式
  (3)[340:usb ffs open]read descriptors      ← adbd 在跑
  ```
  recovery 的 rc 写 `cmode 2` = **HOST_ONLY（主机模式）**，而 adb 需要**设备模式（gadget）**
- **讽刺点**：gadget 和 adbd 全都正常工作，唯一的问题就是控制器角色被切成主机

---

## 四、我的误判（幽灵问题 —— 排查了并不存在的问题）

| # | 我当时的判断 | 真相 |
|---|---|---|
| 19 | "preloader 在 LU0 头部" | ❌ 在 LU1 |
| 20 | "备份文件大多是空的，备份失败了" | ❌ **统计方法错** —— MTK 分区本来就稀疏，数据完全完好 |
| 21 | "`return []` 会导致调用者解包崩溃" | ❌ 三处调用点都有 `if not partinfo` 守卫 |
| 22 | "`boot2` 分支用错 size，应为 `lu1_size`" | ⚠️ 不自洽是确定的，但**正确值无法确定**（XML 路径有两套矛盾设计） |
| 23 | "`musb-hdrc` 这个 UDC 路径不存在，所以 cmode 写入失败" | ❌ 它解析到 `/sys/devices/platform/11201000.usb3/cmode`，**写入成功** |
| 24 | "MTU3 驱动的 UDC 名是 `mtu3`" | ❌ 内核日志显示 `mtu3_phone musb-hdrc`，**`musb-hdrc` 才是对的** |
| 25 | "adb 之前用过，可能被我的 patch 弄坏了" | ❌ 从来没用过 |
| 26 | "整盘读取要 3 小时，所以必须备份" | ❌ 只有 `userdata`(112G) + `super`(6G) 是大头，关键分区仅 ~60MB |

### 关于第 20 项（值得单独说）

我用"每 16KB 块里的全零比例"判断文件是否为空，结果报出：
```
nvram.bin    99.6% 全0   <<< 疑为空
persist.bin  99.8% 全0   <<< 疑为空
proinfo.bin 100.0% 全0   <<< 疑为空
```

**这是错的。** MTK 的 `nvram`/`nvdata`/`persist` 这类分区本来就是**稀疏**的 ——
64MB 分区里只有几十万字节有效数据是正常的。改成统计"非零字节绝对数量"后：
```
nvram.bin   255136 个非零字节   → 内容有效
proinfo.bin     29 个非零字节   → 就是设备标识串，有效
```

**教训**：选指标要谨慎，比例类指标对稀疏数据有误导性。

---

## 五、我造成的实际损失（3 次 bootloop）

| # | 改动 | 结果 | 我的错误 |
|---|---|---|---|
| 27 | 加 7 个 `write` 到不存在的路径 + `on init` 里 `setprop service.adb.root 1` | ❌ bootloop | `setprop service.adb.root 1` 会触发 `restart adbd`，而在 `on init` 阶段 `/system` 还没挂载 → adbd 二进制不存在 |
| 28 | 改 UDC 名 + `on fs` 里加 setprop | ❌ bootloop | UDC 名判断错；且不知道 setprop 是否安全 |
| 29 | **只把 `cmode 2` 改成 `0`** | ❌ bootloop | **只改一个数字也炸** → 说明对 init.rc 的修改不能靠猜 |

**共同根因**：在**看不到设备内部状态**的情况下修改 `init.rc`，
而 Android init 对启动脚本极其敏感，且 bootloop 状态下拿不到日志。

**三次都能救回来**，因为：
- 全程没碰 `preloader`（唯一的单点不可恢复故障）
- `boot_a` / `vbmeta_a` 都有完整备份
- BROM/DA 是硬件级入口，系统坏了也能连

---

## 六、可复用的教训

### 1. 判断设备能否 root：先看"通道"，不是"权限"
```
能写 flash  ≠  能控制系统
真正的入口：bootloader 解锁 / fastboot / adb / recovery / 串口
```
这次我们有**完整的 DA 读写权限**，但**没有任何执行通道** → 依然拿不到 root。

### 2. 分区分级：什么能改，什么绝对不能碰

| 分区 | 风险 | 说明 |
|---|---|---|
| `boot` / `vbmeta` | 🟢 低 | 失败只是开不了机，BROM 可救 |
| `lk` / `tee` / `gz` | 🟠 中 | 失败可能进不去 BROM |
| **`preloader`** | 🔴 **极高** | **真砖，不可恢复** |
| `nvram`/`nvdata`/`proinfo`/`persist` | 🔴 跨机绝对不行 | IMEI 冲突 / 射频校准错误 |

### 3. 为什么 IMEI 不能重复
- IMEI 是蜂窝网络上的**全局唯一标识**
- 重复 → 运营商判定"克隆设备" → **拉黑 IMEI** → 两台都上不了网
- 多数国家把篡改 IMEI 列为违法行为

### 4. 为什么射频必须校准
- 每台设备的晶振频率、PA/LNA 增益、天线匹配阻抗都有**制造公差**
- 出厂时逐台实测，结果写进 `nvram`（`APCFG/APRDEB/` → `BT_Addr`、`WIFI`）
- 不校准 = 搜不到网 / 通话断续 / 可能超法定发射功率

### 5. 为什么不能"直接刷个新系统"
| PC 有什么 | Android 有什么 |
|---|---|
| UEFI / ACPI（固件主动描述硬件） | ❌ 无，靠**设备树**硬编码 |
| 标准件（NVMe / PCIe） | ❌ 全是定制件 |
| 驱动在 OS 里（Windows/Linux 自带几万个） | ❌ 驱动在 `vendor` 分区 |

**GSI 只替换 `system` 层，`vendor` / `kernel` 完全不动** →
新系统的框架和旧 vendor 的接口对不上。

### 6. 强制进 recovery 的方法（MTK）
```
① para 分区偏移 0x00 写 "boot-recovery"     ← 实测有效
② 按键组合（MT65XX_RECOVERY_KEY）
③ RTC 寄存器（需 PMIC I2C 访问，DA 做不到）
```

### 7. 排障利器：`expdb` 分区
- MTK 的崩溃日志分区（这台 20MB）
- 含 **preloader 日志 + 内核日志 + ramoops（pstore）**
- 读法：
  ```python
  dl.readflash(addr=expdb_offset, length=..., filename="expdb.bin")
  # 然后提取可打印字符串
  ```
- **这次就是靠它才定位到 `cmode` = `HOST_ONLY`**

### 8. 硬超时必须外挂
```bash
perl -e 'alarm N; exec @ARGV' python3 script.py
```
Python 的 `signal.alarm` 对阻塞在 C 层的 USB 调用无效。

---

## 最终结论

设备的管控层次如下：

```
层次 1: BL 锁 (seccfg)          🔓 已解锁
层次 2: 签名校验 (SBC/SLA/DAA)   ✅ 全 False, 无阻碍
层次 3: 引导完整性 (AVB)         ✅ 已用 vbmeta flags 绕过
层次 4: 系统接口 (adb/fastboot)  ❌ ★ 卡在这里
层次 5: 应用管控 (设置锁定)      ❌ 更上层, 但它是"因"
```

**层次 1~3 我们全都通过了，卡在层次 4。**
而层次 5（管理 App）是造成层次 4 的原因 —— 因为它是**软件层**，用户自己可以解除。
