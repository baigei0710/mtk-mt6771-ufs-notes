# mtkclient 缺陷记录

在给 MT6771 + UFS 平板做底层操作的过程中，发现 [mtkclient](https://github.com/bkerler/mtkclient)
的 4 个问题。其中一个已提交 PR。

版本：`v2.1.4.1-48-gcd25cf9`

---

## 1. UFS 的 `--parttype` 完全失效 ★ 已提 PR

**PR**：https://github.com/bkerler/mtkclient/pull/388

### 位置
`mtkclient/Library/DA/storage.py`，UFS 分支的 `else` 块（line 319–321）

### 根因：变量遮蔽

```python
else:
    if not xml:
        parttype = UFSPartitionType.USER   # ← 先把字符串覆盖成整数 3
    if parttype == "lu0":                  # ← 再拿整数 3 去比字符串, 永远为 False
        ...
    elif parttype == "lu1":                # ← 永远走不到
```

`UFSPartitionType` 继承自 `IntEnum`，`USER = 3`。所以那行赋值之后，
`parttype` 变成**整数 3**，后面的字符串比较**全部为假**。

### 后果
`lu0` / `lu1` / `lu2` / `lu3` **四个值全部不可达**，控制流总是落到最后的 `else`。

### 影响面
只影响**非 xml（XFLASH）路径** —— 因为 `if not xml:` 是分支里的第一句，
xml 路径会跳过这个赋值。而 XFLASH 正是 UFS 常见路径。

### 附带问题：错误提示也是错的

```python
self.error("Unknown parttype. Known parttypes are \"lu1\",\"lu2\",\"lu3\",\"lu4\"")
```

- `lu4` **根本不存在**（全仓库搜索确认只出现在这一句里，没有任何分支接受它）
- 真能用的 `lu0` **反倒没被列出来**

### 实测影响
UFS 机型上无法用 `--parttype=lu0` 寻址主 LUN0。而 UFS 的 preloader 在 **LU1**，
正经写法 `--parttype=lu1` 也被这个 bug 挡死 → UFS 用户拿不到自己的 preloader。

（旁证：mtkclient 仓库里内置 838 个 preloader，**全是 eMMC 的**
 —— 用 `UFS_BOOT` 标记搜索，命中数为 0）

### 复现
```bash
mtk.py printgpt --parttype=lu0
# → Storage - [LIB]: Unknown parttype. Known parttypes are "lu1","lu2","lu3","lu4"
```
真机实测（MT6771 + Micron UFS 2.2），`lu0` 和 `lu1` 都报同样的错。

隔离复现脚本见 [`tools/repro_ufs_parttype.py`](../tools/repro_ufs_parttype.py)：
把原始判定逻辑逐字转写后跑一遍，四个值全部落到 `else`。

### 修复
先做字符串匹配，再赋枚举值。完整补丁见
[`patches/fix-ufs-parttype.patch`](../patches/fix-ufs-parttype.patch)。

同时修正错误提示，列出真正被接受的值：
`"user","boot1","boot2","rpmb","lu0","lu1","lu2","lu3"`

### 修复验证
打补丁后 `--parttype=lu1` 成功读出 LU1：
- dump 开头是 ASCII `UFS_BOOT`（偏移 `0x0`）
- `MTK_BLOADER_INFO_v36` 在 `0x42eb4`
- 裁出 273204 字节的完整原厂 preloader

---

## 2. CLI 的 `connect()` 路径假失败

### 现象
所有走 DA 的 CLI 命令都报：

```
DaHandler - Please disconnect, start mtkclient and reconnect.
```

### 但直接调 API 是成功的

```python
mtk = Mtk(config=cfg)
mtk.preloader.init()      # → True，握手成功并打印 SOC_ID
```

CLI 走的是 `DaHandler.connect()`，那条路径返回 `None`。

### 出处
`mtkclient/Library/DA/mtk_da_handler.py:131-132`
```python
if mtk.config.target_config is None:
    self.info("Please disconnect, start mtkclient and reconnect.")
```

### 绕过方式
自己写脚本直接调 API，绕开 CLI 的 `connect()`：

```python
from mtkclient.Library.DA.mtk_da_handler import DaHandler
dh = DaHandler(mtk, 20)
m2 = dh.connect(mtk)
if m2 is None or getattr(m2.daloader, "da", None) is None:
    m2 = dh.configure_da(m2 if m2 is not None else mtk)
dl = m2.daloader      # 之后就能 dl.readflash / dl.writeflash
```

### 注意：绕过 CLI 要自己补两处初始化
1. **DA 装载** —— `dl.da` 默认是 `None`，需要 `configure_da()`（内部调 `set_da()`
   创建 `DAXFlash`）
2. **`gpt_settings`** —— `MtkConfig.gpt_settings` 默认 `None`，CLI 靠 `ArgHandler` 填：
   ```python
   from mtkclient.Library.Partitions.gpt import GptSettings
   cfg.gpt_settings = GptSettings(0, 0, 0)   # 0 = 自动探测
   ```

---

## 3. `dumppreloader` 会先把设备砸断

### 现象
运行后设备 USB 断开，报：
```
DeviceClass - [LIB]: Device disconnected
```

### 原因
`mtkclient/Library/mtk_main.py` 的 `dumppreloader` 处理里，第一步就是
`mtk.crasher()`：

```python
elif cmd == "dumppreloader":
    if mtk.preloader.init():
        rmtk = mtk.crasher()      # ← 在这里
```

而 `crasher()` 在判定"不在 BROM"时会调 `plt.crash(0)`，也就是往地址 0
发 0x100 字节全 0（`exploit_handler.py:146-150`）：

```python
def crash(self, mode=0):
    self.info("Crashing da...")
    try:
        if mode == 0:
            self.mtk.preloader.send_da(0, 0x100, 0x100, b'\x00' * 0x100)
```

### 实测
在 MT6771 上反复触发过，设备会断开且需要重插。

---

## 4. 没有 reboot / shutdown 的 CLI 命令

### 现象
`mtk.py da` 的子命令列表里**没有** reboot：

```
{peek,efuse,generatekeys,keyserver,meta,vbmeta,nvitem,patchmodem,imei,rpmb,
 memdump,memdram,dumpbrom,poke,seccfg}
```

### 但底层能力是有的
- `xflash_lib.py:808-833` 的 `ShutDownModes`：
  ```python
  class ShutDownModes:
      NORMAL = 0
      HOME_SCREEN = 1
      FASTBOOT = 2
  ```
- `mtk_da_handler.py:1384` 内部用过 `mtk.daloader.shutdown(bootmode=0)`

### 绕过方式
自己写脚本：
```python
dl.shutdown(bootmode=2)     # 尝试进 fastboot
```

### 实测结果
`bootmode=2` 在 MT6771 上**无效** —— 屏幕全黑，`fastboot devices` 为空。
原因：mtkclient 的 `MtkBootModeFlag` 只定义了 `0:normal, 1:meta`，
**根本没有 recovery 这个值**，所以这条指令无法用于进 recovery。

---

## 另外两个值得注意的点（不算 bug，但容易踩）

### A. UFS 的 `partitiontype_and_size` 变量遮蔽同源问题
`storage.py:290-340` 的 UFS 分支在 xml 路径下还有一处**死代码**：

```python
if parttype == "lu0":
    if xml:
        parttype = "UFS-LUA0"    # ← 永不可达
```

原因：xml 路径下 `parttype` 早在 line 295-297 就被设成 `"UFS-LUA2"` 了，
所以 `parttype == "lu0"` 比较失败，进不来。

同时这也**暴露了语义矛盾**：line 296 说 xml 下用户区叫 `UFS-LUA2`，
而 line 323-324（死代码）说叫 `UFS-LUA0`。

如果对照 `xml_lib.py:788-791` 里内置的实测样本：
```
lua0_size = 0x400000      (4MB)      ← boot LUN
lua1_size = 0x400000      (4MB)      ← boot LUN
lua2_size = 0xee5800000   (59GB)     ← 主数据区
```
以及 `xml_lib.py:808-811` 的 `lua0_size → lu0_size` 直接映射，
**`LUA` 与 `LU` 的命名关系需要维护者澄清**，不宜自行猜测。

### B. `return []` 与调用者的返回值约定不一致
错误路径 `return []`，而正常路径返回 3 元组 `(storage, parttype, length)`。

不过**实测三处调用点都有守卫**，所以不会崩：
```python
# xflash_lib.py:711 / 717   readflash
# xflash_lib.py:851         writeflash
# xflash_lib.py:351         formatflash
if not partinfo:
    return False
```
所以这条**不是**实际缺陷，只是代码一致性问题。
