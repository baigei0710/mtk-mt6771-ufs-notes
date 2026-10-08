# 修改记录 02：recovery 的 `cmode`（失败的尝试）

> **结果**：❌ **3 次 bootloop，全部失败**

记录下失败的过程和原因 —— 这部分同样有参考价值。

---

## 背景

进了 recovery 之后 **USB 完全不枚举**（Mac 侧零反应）。
从 `expdb` 内核日志定位到根因：

```
(4)[1:init]gadgets_make name=g1
(4)[1:init]function_make name=ffs.adb
(4)[1:init]musb_cmode_store NORMAL --> HOST_ONLY     ← ★
(3)[340:usb ffs open]read descriptors
```

recovery 的 `init.recovery.mt6771.rc` 写了 `cmode 2` = **HOST_ONLY（主机模式）**，
而 adb 需要**设备模式（gadget）**。

原始内容：
```
on fs && property:ro.debuggable=0
    # distinguish USB shoulde connect or not, i.e. CDP vs SDP
    write /sys/class/udc/musb-hdrc/device/cmode 2
    # set charging free due to it wait for USB activation
    start adbd
```

---

## 三次尝试（全部 bootloop）

### 尝试 1：加 7 个 write + `on init` 里 setprop ❌

```diff
 on init
     setprop sys.usb.configfs 1
     setprop sys.usb.ffs.aio_compat 1
+    setprop service.adb.root 1
+    setprop sys.usb.config adb
+    setprop sys.usb.state adb
 
-on fs && property:ro.debuggable=0
+on fs
+    write /sys/class/udc/musb-hdrc.0.auto/device/cmode 2
+    write /sys/class/udc/musb-hdrc.1.auto/device/cmode 2
+    write /sys/class/udc/11200000.usb/device/cmode 2
+    ... （共 7 个候选路径）
```

**估计的失败原因**：
`setprop service.adb.root 1` 会触发 init.rc 里的
```
on property:service.adb.root=1
    restart adbd
```
而在 `on init` 阶段 **`/system` 还没挂载** → `adbd` 二进制不存在 →
启动失败 → **bootloop**

### 尝试 2：改 UDC 名 + `on fs` 里 setprop ❌

```diff
-    write /sys/class/udc/musb-hdrc/device/cmode 2
+    write /sys/class/udc/mtu3/device/cmode 2
+    setprop sys.usb.controller mtu3
+    setprop sys.usb.config adb
```

**基础判断就错了**：我根据主线的 MTU3 驱动源码
（`mtu3.h` 里 `#define MTU3_DRIVER_NAME "mtu3"`）推断 UDC 名是 `mtu3`。

**但内核日志显示**：
```
(1)[1:swapper/0]mtu3_phone musb-hdrc: dr_mode: 3, is_u3_dr: 0
```
→ MTK 的下游驱动用的是 musb 兼容命名，**UDC 名是 `musb-hdrc`**。

### 尝试 3：只把 `cmode 2` 改成 `0` ❌

```diff
-    write /sys/class/udc/musb-hdrc/device/cmode 2
+    write /sys/class/udc/musb-hdrc/device/cmode 0
```

**只改了一个数字，还是 bootloop。**

而且这次改动**经字面替换生成**，除了这一行之外与原文件逐字节相同：
```
diff 显示只有 cmode 那一处（含添加的注释）
结构核对：大小/AVB0/AVBf/kernel/AVB tail 全部一致
```

**结论：`cmode` 的值 `0` 在这个驱动上可能非法**，或者 init.rc 的任何修改
在这个设备上都会破坏启动 —— **但没有设备日志无法确认**。

---

## 为什么这条路走不通

```
需要 adb 才能拿设备日志
    ↓
但 adb 不通正是我们要解决的问题
    ↓
死循环
```

**bootloop 状态下拿不到日志**，所以每次改动都是纯盲试。

---

## 失败中的发现（有价值的部分）

### 1. 被日志纠正的两个错误判断

| 我的判断 | 真相 |
|---|---|
| `musb-hdrc` 路径不存在 | ❌ 它解析到 `/sys/devices/platform/11201000.usb3/cmode`，**写入成功** |
| UDC 名是 `mtu3` | ❌ 内核日志 `mtu3_phone musb-hdrc`，**`musb-hdrc` 才对** |

（两者都是通过读 `expdb` 日志确认的）

### 2. `init.recovery.mt6771.rc` 在正常启动时也会加载

`init.rc` 第一行：
```
import /init.recovery.${ro.hardware}.rc
```
→ 所以改动的影响面**比预想的大**（不只影响 recovery）。

### 3. `cmode` 的真实语义

```
/sys/devices/platform/11201000.usb3/cmode
  值 0 = NORMAL
  值 2 = HOST_ONLY      ← recovery 用的是这个
```
驱动函数：`musb_cmode_store`（内核符号表里能搜到）

### 4. Android init 的 `write` 会创建文件
```c
OpenFile(args[1], O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600)
```
注意 `O_CREAT` —— 但父目录不存在时仍会失败（`ENOENT`，仅记日志）。

---

## 三次 bootloop 都能救回来

因为：
- **全程没碰 `preloader`**（唯一的单点不可恢复故障）
- `boot_a` / `vbmeta_a` 都有完整备份，写回即可
- **BROM/DA 是硬件级入口**，系统坏了也能连

回退命令：
```python
dl.writeflash(addr=0x20a00000, length=len(orig_boot), filename="", wdata=orig_boot)
dl.writeflash(addr=0x14500000, length=len(orig_vbmeta), filename="", wdata=orig_vbmeta)
```

---

## 教训

**不要在没有设备侧日志的情况下盲改 `init.rc`。**

- Android init 对启动脚本极其敏感
- bootloop 状态下拿不到日志
- 每次盲试的代价是一个完整的"写入 → 重启 → 观察 → 回退"循环

**正确的顺序应该是先打通 adb（哪怕只有 shell），再改这类文件。**
