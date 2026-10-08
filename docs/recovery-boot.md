# MTK 进入 Recovery 的机制与实测方法

**核心发现**：MTK 的 bootloader **不读标准 Android BCB 的 `command` 字段**，
但往 `para` 分区偏移 `0x00` 写 `boot-recovery` **实测有效**。

---

## 一、MTK 判定 recovery 的三种机制

从 MTK 的 LK 源码（`platform/*/lk/boot_mode.c`）确认，只有这三条路径：

```c
/*Check RTC to know if system want to reboot to Recovery*/
if(Check_RTC_Recovery_Mode())
{
    g_boot_mode = RECOVERY_BOOT;
    return;
}

#ifdef MT65XX_RECOVERY_KEY
    if(mtk_detect_key(MT65XX_RECOVERY_KEY))
    {
        g_boot_mode = RECOVERY_BOOT;
        return;
    }
#endif
```

| # | 机制 | 可行性 |
|---|---|---|
| 1 | **RTC 寄存器**（`Check_RTC_Recovery_Mode()`） | ❌ DA 做不到（走 PMIC I2C，不是内存映射） |
| 2 | **物理按键**（`MT65XX_RECOVERY_KEY`） | ⚠️ 需要硬件操作 |
| 3 | `unshield_recovery_detection()` | ❓ 需要写 misc 的私有结构 |

**注意：没有读取标准 Android BCB 的代码。** 这就是为什么在 `para` 里
找不到标准 BCB 的 `command` 字段。

### 启动模式的枚举值
`boot_mode.h`：
```c
typedef enum
{
    NORMAL_BOOT = 0,
    META_BOOT = 1,
    RECOVERY_BOOT = 2,      // ← 注意是 2
    SW_REBOOT = 3,
    FACTORY_BOOT = 4,
    ADVMETA_BOOT = 5,
    ATE_FACTORY_BOOT = 6,
    ALARM_BOOT = 7,
} BOOTMODE;
```

**但这个枚举是 LK 内部的**，mtkclient 的 DA 协议里用的是另一套：
```python
class MtkBootModeFlag:
    boot_mode = b"\x00"     # 0:normal, 1:meta
```
—— **没有 recovery**，所以 DA 的 shutdown 指令无法用于进 recovery。

---

## 二、实测有效的方法：写 `para` 的 BCB

### 原理
Android bootloader 会读 misc/para 分区偏移 `0x00` 的 `command` 字段：

```
偏移 0x00 : command[32]      ← 写 "boot-recovery" 进 recovery
偏移 0x20 : flags[32]
偏移 0x40 : recovery[768]
偏移 0x340: stage[32]
偏移 0x840: status[4]
```

本机的 `para` 分区（512KB）**偏移 0x0000–0x0800 是全零**
（MTK 的参数头 `BCAB` 从 `0x804` 才开始），所以这块是空闲可用的。

### 写入方法

```python
# 1. 构造 BCB
bcb = bytearray(0x800)
cmd = b"boot-recovery\x00" + b"\x00" * 18       # command[32]
bcb[0:len(cmd)] = cmd

# 2. 读出当前 para, 只改前 0x800
dl.readflash(addr=para_offset, length=para_size, filename="para.bin")
data = bytearray(open("para.bin","rb").read())
data[0:0x800] = bcb

# 3. 写回
dl.writeflash(addr=para_offset, length=len(data),
              filename="", wdata=bytes(data))
```

`para` 分区的偏移从 GPT 读（本机是 `0x108000`）。

### 实测结果
**✅ 成功进入 Android Recovery。**

而且这个命令是**一次性**的 —— recovery 用完（或 bootloader 判定已执行）后
BCB 会被清空，下次启动回到正常模式。

---

## 三、`para` 分区的真实结构（本机 dump）

```
偏移 0x0000 - 0x0800 : 全零                        ← ★ BCB 区（可用）
偏移 0x0804          : "BCAB"                      ← MTK 参数头
偏移 0x0808          : 01 02 00 00
偏移 0x0810          : 9f 00 9e 00
偏移 0x081c          : 3c 65 42 ca (可能是校验)
偏移 0x20000         : "ENV_v1"                    ← 环境变量区
                        "off-mode-charge=1"
偏移 0x23ff4         : 少量数据
偏移 0x40000         : 一串带签名的数据（含时间戳/校验）
```

**注意**：`BCAB` 是 MTK 私有格式，不是标准 Android 的 misc 布局。
`0x40000` 那段带签名，**不要乱写**。

---

## 四、进 recovery 之后：USB 为什么不通

本机实测：进了 recovery，但 **USB 完全不枚举**（Mac 侧零反应）。

### 从 `expdb` 内核日志拿到的根因

```
(4)[1:init]gadgets_make name=g1              ← gadget 建好了
(4)[1:init]function_make name=ffs.adb        ← adb 功能建好了
(4)[1:init]file system registered
(4)[1:init]config_desc_make name=b.1
(4)[1:init]musb_cmode_store NORMAL --> HOST_ONLY    ← ★ 被切成主机模式
(4)[1:init][MTU3]mailbox state(3)
(3)[340:usb ffs open]read descriptors        ← adbd 在跑, 在读描述符
audit: avc: denied { write } for pid=1 comm="init" name="cmode"
audit: ... path="/sys/devices/platform/11201000.usb3/cmode"
```

**recovery 的 init 脚本写 `cmode 2`，值是 `HOST_ONLY`（主机模式）。**
而 adb 需要**设备模式（gadget）**。

所以：**gadget 和 adbd 全都正常工作，唯一的问题是 USB 控制器角色被切成主机。**

### recovery 的原始 init 内容
`init.recovery.mt6771.rc`：
```
on fs && property:ro.debuggable=0
    # distinguish USB shoulde connect or not, i.e. CDP vs SDP
    write /sys/class/udc/musb-hdrc/device/cmode 2
    # set charging free due to it wait for USB activation
    start adbd
```

**注意两点（我一开始都判断错了）：**
1. `musb-hdrc` **是正确的 UDC 名** —— 内核日志显示 `mtu3_phone musb-hdrc`
   （MTK 的 MTU3 驱动用了 musb 兼容命名）
2. 这个路径**解析成功** —— 实际落到 `/sys/devices/platform/11201000.usb3/cmode`

### 尝试修改 → bootloop

| 改动 | 结果 |
|---|---|
| 把 `cmode 2` 改成 `cmode 0` | ❌ **bootloop** |
| 加 `setprop sys.usb.config adb` | ❌ bootloop |
| 改 UDC 名 + 7 个 write 尝试 | ❌ bootloop |

**只改一个数字也 bootloop** → 说明对 `init.rc` 的修改不能靠猜，
而且 bootloop 状态下拿不到设备日志。

**这条路的结论**：需要设备侧日志才能继续，但不通 adb 就拿不到日志（死循环）。

---

## 五、其他尝试（都失败）

| 方法 | 结果 |
|---|---|
| `SHUTDOWN(bootmode=2)` | ❌ 屏幕全黑，未进 fastboot 也未进 recovery |
| 按键组合 | ❌ 进不去（设备管控可能屏蔽） |
| 写 `misc` 标准 BCB | ⚠️ 实际是 `para`，已用 BCB 方法成功 |
| 通过"破坏系统"触发 recovery 回退 | ❌ **机制上不成立**（LK 没有这个逻辑） |

### 关于"破坏系统进 recovery"
一度想通过损坏 boot/system 来让 bootloader 回退到 recovery，但读了
`boot_mode.c` 后确认：**MTK 的 LK 里没有"检测到系统损坏就进 recovery"这段逻辑。**
损坏系统只会得到卡启动/黑屏/进 BROM，**不会进 recovery**。

---

## 六、总结

**能用的方法**：
```python
# para 分区偏移 0x00 写 "boot-recovery"
data[0:32] = b"boot-recovery\x00" + b"\x00" * 18
```

**但进 recovery 之后 USB 不通**（`cmode` = HOST_ONLY），
而改 `cmode` 会导致 bootloop —— 所以这条路最终没能走通。

**务实结论**：如果设备有 adb，`adb reboot recovery` 是最简单的方式，
完全不需要碰 `para`。
