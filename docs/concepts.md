# 基础概念入门

写给不了解 Android / MTK 底层的人。**用这次实际折腾的设备做例子**，所以都是具体可验证的。

阅读顺序建议：先看[启动链](#二启动链从上电到桌面)，那是理解一切的基础。

---

## 一、MTK（联发科）是什么

**MediaTek（联发科）** 是台湾的芯片设计公司，做手机/平板 SoC（System on Chip）。
市面上大量中低端安卓设备用它，例如：

| 芯片 | 别名 | 常见设备 |
|---|---|---|
| MT6771 | Helio P60 | 中端手机 / 平板 / 学习机 |
| MT6765 | Helio P35 | 入门机 |
| MT8788 | — | 平板（与 MT6771 同源） |

**MTK 的"性格"**（和 Qualcomm 对比）：

| 特点 | MTK | Qualcomm |
|---|---|---|
| 底层工具 | mtkclient / SP Flash Tool | QFIL / edl |
| 下载模式 | BROM / preloader | EDL（9008） |
| 工具链开放性 | 社区工具成熟（mtkclient） | 相对封闭 |
| 固件格式 | scatter + 分区镜像 | rawprogram.xml |

**对玩家来说 MTK 的意义**：它的 **BROM（Boot ROM）有个著名的漏洞通道**，
配合 mtkclient 可以在**不知道任何密码**的情况下读写 flash。这就是我们能做所有事的基础。

---

## 二、启动链：从上电到桌面

**这是理解一切的核心。** 安卓设备的启动是**层层接力**，每一层负责初始化一部分硬件，
然后把控制权交给下一层。

```
按下电源键
    ↓
① BROM        （芯片内部固化的 ROM，不可修改）
    ↓ 校验并加载
② preloader   （在 flash 的 boot LUN 里）
    ↓ 初始化 DRAM / PMIC / 存储
    ↓ 校验并加载
③ LK          （Little Kernel，就是 bootloader）
    ↓ 读取启动模式、校验并加载
④ boot        （Linux 内核 + ramdisk）
    ↓ 挂载分区、启动 init
⑤ system      （Android 框架，最上层）
    ↓
桌面
```

### ① BROM（Boot ROM）

- **固化在芯片内部**，出厂后不可改、不可删
- 作用：最基础的硬件初始化，然后从存储里读第一段代码
- **MTK 的 BROM 有个可利用的漏洞** → 这就是 mtkclient 能读写任意分区的原因
- **只要 BROM 还在，设备就永远能被救**（这是"变砖"讨论的关键）

**进入方式**：设备断电，按住特定键插 USB。或者系统损坏时自动进入。

### ② preloader

- **位于 flash 的 boot region**（eMMC 是 `boot0`/`boot1`；**UFS 是 LU1**）
- 作用：初始化 **DRAM**（内存）、PMIC（电源）、存储控制器
- **它是设备专属的** —— 里面绑定了这块板子的 DRAM 时序和 PMIC 配置
- **所以跨机型刷 preloader = 必然变砖**

**USB 下载窗口只有 0.3 秒** ← 这是为什么用 mtkclient 时必须"先进入等待，再插线"

> **为什么 preloader 这么关键**：它是启动链里**第一个和硬件强绑定**的一层。
> 前面的 BROM 是通用的，后面的 LK/boot 都能换，但 preloader 换了就动不了 DRAM。

### ③ LK（Little Kernel）

- 就是通常说的 **bootloader（引导程序）**
- 作用：
  - 读取启动模式（正常 / recovery / fastboot / 工厂模式）
  - 校验 boot 镜像的签名（AVB）
  - 根据 slot 选择启动哪个分区（`_a` 还是 `_b`）
  - 把 cmdline 和 DTB 传给内核
- **它是内存里唯一能决定"下一步启动什么"的环节**

### ④ boot（内核 + ramdisk）

- **kernel**：Linux 内核
- **ramdisk**：一个小的根文件系统（gzip + cpio 格式），内核启动早期用它
  - 里面主要是 `init` 程序和启动脚本（`init.rc`、`prop.default`）
  - Android 11 的机型经常是 **first-stage ramdisk**（只做第一阶段，然后切到 `system`）

**在 PC 上类比**：boot 分区 ≈ 内核 + initrd

### ⑤ system / vendor / product

- `system`：Android 框架（Java 层、系统 App）
- `vendor`：**硬件抽象层（HAL）**，厂商为这个型号编译的驱动和中间层
- `product`：产品定制部分

**关键点**：**`vendor` 是绑定到具体硬件的**。
换 `system`（比如刷 GSI）时 `vendor` 不动，所以新系统可能用不了旧驱动。

---

## 三、BL（Bootloader）与"解锁"

### BL 是什么

**BL = Bootloader = 上面启动链里的 ③（LK）。**

通常说"BL 锁"指的是：**bootloader 是否强制校验即将启动的镜像签名**。

| 状态 | 含义 |
|---|---|
| 🔒 锁定 | 只启动官方签名的镜像，拒绝任何修改 |
| 🔓 解锁 | 允许启动任意（未签名）镜像 |

### 解锁状态存在哪

**存在 `seccfg` 分区**里（MTK 的格式，本机魔数是 `MMMM`）。

所以"一键解 BL"的工具做的事其实很简单：
```
用 BROM 漏洞获得 flash 写权限
    ↓
改 seccfg 分区里的锁标志
    ↓
写回 → 重启 → bootloader 认为已解锁
```

**没有"破解"什么高深的东西，就是改一个分区里的几个字节。**

### ⚠️ 但解锁 ≠ 能 root

**本机的情况恰好证明了这一点**：

```
SLA enabled: False     ← 无签名校验
SBC enabled: False
DAA enabled: False     ← 已经是"解锁"状态
```

**但依然拿不到 root** —— 因为缺的不是"锁"，是"**执行通道**"（见[第六节](#六执行通道vs-写权限)）。

**解锁只是"允许你换轮胎"，但你还需要"能打开车门"。**

---

## 四、fastboot

### 是什么

**fastboot 是一个协议 + 主机端工具**，用来在 bootloader 阶段刷写分区。
由 Google 制定，几乎所有 Android 设备都支持。

```bash
fastboot devices                    # 看设备
fastboot flash boot boot.img        # 刷 boot 分区
fastboot reboot                     # 重启
```

### 两种模式的区别

| | bootloader fastboot | fastbootd（userspace） |
|---|---|---|
| 运行环境 | bootloader 里 | Android 的 recovery 环境里 |
| 能力 | 刷物理分区 | **能操作 super 内部的逻辑分区** |
| 进法 | 按键 / `adb reboot bootloader` | `fastboot reboot fastboot` |
| 需要 | bootloader 实现该协议 | 系统支持 + bootloader 支持 |

**刷 GSI 必须用 fastbootd** —— 因为 `system` 是 `super` 里的**逻辑分区**，
普通 fastboot 看不到它。

### 本机的情况

❌ **bootloader 根本不实现 fastboot。**

实测：用 mtkclient 发 `SHUTDOWN(bootmode=2)`（我以为 2 = fastboot），
结果屏幕全黑，`fastboot devices` 为空。

**根源**：MTK 的 DA 协议里根本没有 recovery 这个 bootmode 值
（mtkclient 的 `MtkBootModeFlag` 只定义了 `0:normal, 1:meta`）。

**教训**：**"协议标准" 不等于 "设备都实现了"** —— 定制设备可能阉割功能。

---

## 五、BCB（Bootloader Control Block）

### 是什么

**BCB 是 Android 用来"告诉 bootloader 下次启动到哪里"的一块数据。**

位置：**`misc` 分区**（MTK 上这个分区常常叫 `para`）。

结构（标准 Android）：
```
偏移 0x00 : command[32]      ← 命令字符串
偏移 0x20 : flags[32]
偏移 0x40 : recovery[768]    ← 传给 recovery 的参数
偏移 0x340: stage[32]
偏移 0x840: status[4]
```

### 怎么用

**最经典的用法：往 `command` 写 `boot-recovery` 就能进 recovery。**

```python
# 构造 BCB（前 0x800 字节）
bcb = bytearray(0x800)
cmd = b"boot-recovery\x00" + b"\x00" * 18        # command[32]
bcb[0:len(cmd)] = cmd

# 写进 misc/para 分区
dl.writeflash(addr=para_offset, length=..., filename="", wdata=data_with_bcb)
```

**这是一个"一次性"命令** —— recovery 用完（或 bootloader 判定已执行）后会被清空。
（本机实测确认：进 recovery 后 BCB 自动清了，下次开机回正常模式。）

### ⚠️ MTK 的特别之处

**MTK 的 bootloader 不读标准 BCB 的 `command` 字段！**

从 MTK 的 `boot_mode.c` 源码确认，它只用这三种机制判定 recovery：
```c
Check_RTC_Recovery_Mode()                // RTC 寄存器
mtk_detect_key(MT65XX_RECOVERY_KEY)      // 物理按键
unshield_recovery_detection()            // misc 中断恢复
```

**但是**：往 `para` 偏移 `0x00` 写 `boot-recovery` **实测有效**
（本机的 `para` 前 0x800 是空的，MTK 的参数头 `BCAB` 从 `0x804` 才开始）。

**所以 MTK 大概是"两条路径都读"**，只是标准那条没写进公开源码。

---

## 六、"执行通道" vs "写权限"

**这是本次折腾最重要的认知。**

### 两者的区别

```
写权限   = 能读写 flash 上的数据        ← 我们有（完整的 DA）
执行通道 = 能让系统按你的意志执行代码    ← 我们缺
```

**有写权限不等于能控制设备。**

打个比方：

> 你能改大楼的**钥匙**（改 flash），
> 但门禁的**判断逻辑**在大楼管理处（运行中的 init / SELinux / bootloader）。
> **你改钥匙没用，因为门禁根本不问你的钥匙。**

### 判断一台设备能否 root 的正确顺序

**先看通道，再看权限：**

| 顺序 | 问题 | 说明 |
|---|---|---|
| 1 | bootloader 能解锁吗？ | 决定能不能换 boot 镜像 |
| 2 | 有 fastboot 吗？ | 决定能不能刷分区 |
| 3 | 能开 USB 调试（adb）吗？ | **决定能不能执行命令** |
| 4 | 有 recovery / 串口吗？ | 极端情况下的后备通道 |
| ✗ | ~~能写 flash 吗？~~ | **这个反而不重要** —— BROM 漏洞通常都能给你 |

**本机的诊断结果**：1 ✅ / 2 ❌ / 3 ❌ / 4 ❌ → **写权限再多也没用。**

---

## 七、IMEI 与射频校准（为什么不能跨机刷）

### IMEI

- **15 位数字，全球唯一**，是设备在蜂窝网络上的身份标识
- 存在 `nvdata` 分区里
- **重复会出什么事**：
  - 两台设备抢同一个 IMEI → 互相把对方踢下线
  - 运营商判定为"克隆设备/被盗机" → **拉黑 IMEI** → 两台都上不了网
  - 多数国家把篡改 IMEI 列为**违法行为**

### 射频校准（RF Calibration）

**为什么需要**：每台设备的射频前端都有**制造公差**

```
晶振频率偏差、PA(功率放大器)增益、LNA(低噪放)增益、天线匹配阻抗
        ↓
每台设备的实测值都不同
        ↓
出厂时逐台测量，把结果写进 flash
```

存在 `nvram` 分区里，路径类似：
```
/mnt/vendor/nvdata/APCFG/APRDEB/BT_Addr        蓝牙 MAC + 功率校准
/mnt/vendor/nvdata/APCFG/APRDEB/WIFI           WiFi MAC + 功率校准
/mnt/vendor/nvdata/APCFG/APRDEB/WIFI_CUSTOM    额外参数
```

**不校准的后果**：

| 项目 | 后果 |
|---|---|
| 发射功率不准 | 搜不到网、信号极差、通话断续 |
| 频率偏移 | 基站无法解调 → 根本连不上 |
| WiFi/BT 功率错 | 连接不稳、耗电剧增 |
| 严重时 | 可能超出法定发射功率（合规问题） |

### 结论

**`nvram` / `nvdata` / `proinfo` / `persist` 这些分区，跨机刷 = 联网功能报废。**

它们的价值在于：**全世界只有你这台机器有这份数据，网上找不到。**

---

## 八、分区逐一说明

以本机为例（Android 11 A/B + 动态分区布局）。

### 引导相关

| 分区 | 作用 | 改它的风险 |
|---|---|---|
| `preloader` | 初始化 DRAM/PMIC（**在 LU1，不在 GPT 里**） | 🔴 **极高，改了真砖** |
| `lk_a` / `lk_b` | bootloader（LK） | 🟠 中，坏了可能进不去 BROM |
| `boot_a` / `boot_b` | 内核 + ramdisk | 🟢 低，失败只是开不了机 |
| `dtbo_a` / `dtbo_b` | 设备树覆盖（DTB overlay） | 🟡 中 |
| `vbmeta_a` / `vbmeta_b` | **AVB 校验元数据** | 🟢 低 |
| `vbmeta_system` / `vbmeta_vendor` | 分区链式校验 | 🟢 低 |
| `tee_a` / `tee_b` | TEE（可信执行环境） | 🟠 中 |
| `gz_a` / `gz_b` | GZ（另一个安全固件） | 🟠 中 |

### 设备身份 / 校准（★ 绝不能跨机）

| 分区 | 作用 |
|---|---|
| `nvram` | **射频校准**（BT/WiFi MAC + 功率） |
| `nvdata` | **IMEI** + NV 配置数据 |
| `nvcfg` | NV 配置 |
| `proinfo` | **设备标识**（型号串、序列号） |
| `persist` | 传感器校准 + 指纹等 |
| `seccfg` | **bootloader 锁状态** |
| `frp` | Factory Reset Protection |
| `otp` | 一次性可编程区 |

### 系统

| 分区 | 作用 |
|---|---|
| **`super`** | **动态分区容器**，内部有 `system` / `vendor` / `product` 等逻辑分区 |
| `userdata` | 用户数据（**加密的**，密钥在 `metadata`） |
| `metadata` | 加密密钥 + 元数据 |
| `md_udc` | 也是 metadata 相关 |

### 其他

| 分区 | 作用 |
|---|---|
| `boot_para` | boot 参数 |
| `para` / `misc` | **BCB**（启动模式控制） |
| `expdb` | **崩溃日志**（preloader + 内核 + ramoops）← 排障利器 |
| `logo` | 开机 logo |
| `md1img` | **基带固件**（调制解调器） |
| `spmfw` | 电源管理固件 |
| `scp` / `sspm` | 协处理器固件 |
| `cam_vpu1~3` | 摄像头 VPU 固件 |

---

## 九、其他重要术语

### A/B 分区（无缝更新）

设备有两套系统分区（`_a` 和 `_b`），更新时写另一套，成功后切换。

```
boot_a / boot_b       system_a / system_b
vbmeta_a / vbmeta_b   ...
```

当前用哪套由 `slot` 决定（本机：`androidboot.slot=a`）。

**好处**：OTA 更新时系统还能正常用，失败了可以回滚。

### 动态分区（dynamic partitions）

传统 Android 每个分区固定大小。Android 10+ 改成把 `system`/`vendor`/`product`
等塞进一个叫 `super` 的容器里，**大小可以动态调整**。

```
super (6GB)
  ├── system_a
  ├── vendor_a
  ├── product_a
  └── ...
```

**这就是刷 GSI 必须用 fastbootd 的原因** —— 普通 fastboot 操作的是物理分区，
而 `system` 现在是 `super` 里的逻辑分区。

### 虚拟 A/B（virtual A/B）

A/B 的压缩版：不真正复制整个系统，而是用 **COW（Copy-On-Write）快照**。
特征：分区名里会出现 `-cow` 后缀。

```
system_a   system_b   system_a-cow   system_b-cow
```

### AVB（Android Verified Boot）

**校验启动链完整性的机制。**

每一层的哈希写在下一层里，签名用厂商私钥。改任何东西校验都会失败。

**绕过方式**：不是伪造签名（私钥拿不到），而是**关闭校验**：
```
vbmeta 分区偏移 0x78 处的 flags 字段：
  bit0 = disable-verity
  bit1 = disable-verification
设置为 3 → 两个都关
```
（bootloader 在已解锁状态下会接受这个标志）

### DA（Download Agent）

**MTK 的下载代理**：一段运行在设备上的程序，通过 USB 和主机通信，执行读写 flash 等操作。

流程：BROM → 上传 DA stage1 → 上传 DA2 + 扩展 → 之后所有操作由 DA 执行。

**本机实测**：DA 能读写任意分区（包括 boot/preloader），
而且即使 Android 系统损坏也能用 —— **因为它是硬件级的**。

### ramdisk / first-stage ramdisk

`boot` 分区里的一个小根文件系统（gzip + cpio）。

- **传统**：它是主 init 所在
- **Android 11+ first-stage 型**：它只做第一阶段初始化，然后 `exec` 到 `system` 里的 init

**重要影响**：first-stage ramdisk 里的 `prop.default` **优先级最低**，
会被 `/system/build.prop` 覆盖。

### slot / slot_suffix

当前使用 A/B 的哪一套。本机是 `a`（`_a` 后缀）。

### recovery

一个最小系统，用于：
- 恢复出厂设置
- 通过 ADB 或 SD 卡刷 OTA 包
- 挂载分区做维护

**通常不包含在正常启动流程里**，需要特殊方式进入。

---

## 十、一句话总结各概念的层次

```
       [ BROM ]                    ← 硬件固化，永远可用（最后的救命稻草）
          ↓
     [ preloader ]                 ← 绑定硬件（DRAM/PMIC），跨机必砖
          ↓
    [ BL / LK ]                    ← 决定启动什么，锁状态在 seccfg
          ↓
   [ fastboot ]                    ← BL 提供的刷机协议（可能被阉割）
          ↓
    [ boot ]                       ← 内核 + ramdisk，改了要处理 AVB
          ↓
  [ super/system ]                 ← 系统本体，动态分区
          ↓
     [ adb ]                       ← 系统提供的执行通道（管控常在这里堵）
          ↓
      [ root ]
```

**想拿到 root，需要从下往上打通每一层。这一次我们通到了 `boot`，
但 `fastboot` 和 `adb` 这两条通道被堵死了，所以最终没能成功。**

---

## 相关文档

- [启动链实测细节：UFS 的 preloader 在哪](ufs-boot-layout.md)
- [BCB 与 MTK recovery 机制](recovery-boot.md)
- [本次遇到的所有问题](problems.md)
