# mtkclient 缺陷记录

在给 MT6771 + UFS 平板做底层操作的过程中，发现 [mtkclient](https://github.com/bkerler/mtkclient)
的若干问题。

版本：`v2.1.4.1-48-gcd25cf9`

## 已提交上游

| 链接 | 内容 | 状态 |
|---|---|---|
| [PR #388](https://github.com/bkerler/mtkclient/pull/388) | 修 UFS `--parttype` 变量遮蔽（非 xml 路径） | 🔵 open |
| [Issue #391](https://github.com/bkerler/mtkclient/issues/391) | xml 路径的 `LUA`/`LU` 命名不一致 + 死代码 + size 配对错误 | 🔵 open |

（Issue #391 是在 PR #388 之后写的 —— 审同一段代码时发现 xml 路径有另一套独立问题，
且我无法实测 xml 路径，所以报告而非猜测。）

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

> 这两处已在 **[issue #391](https://github.com/bkerler/mtkclient/issues/391)** 中正式跟踪，
> 并在 [PR #388](https://github.com/bkerler/mtkclient/pull/388) 里加了交叉引用。
> 都是 **xml 路径**的问题，我的设备不支持 XML/DA-extension 模式，无法实测。

### A. xml 路径的 `LUA`/`LU` 命名不一致 ★

**这是我在写 issue 时才发现的问题，比我 PR 里描述的要严重。**

`storage.py` 的 XML 分支把 `user`/`boot1`/`boot2` 和 `lu0`/`lu1`/`lu2` 当成**不同的 LUN**，
但在非 xml 路径里它们是**同一个 LUN 的别名**：

| CLI 参数 | xml 值 | 非 xml（PR 修复后） | 一致？ |
|---|---|---|---|
| `user` | `UFS-LUA2` | LUN0 | — |
| `lu0` | `UFS-LUA0`（死代码） | LUN0 | ❌ |
| `boot1` | `UFS-LUA0` | LUN1 | — |
| `lu1` | `UFS-LUA1` | LUN1 | ❌ |
| `boot2` | `UFS-LUA1` | LUN2 | — |
| `lu2` | `UFS-LUA2` | LUN2 | ❌ |
| `rpmb` / `lu3` | `UFS-LUA3` | LUN3 | ✅ |

**三组别名在 xml 路径下全部指向不同的 LUA。**

### 判断依据：MTK 自己的内嵌样本

`xml_lib.py:786-793` 内嵌了一段真机响应：

```xml
<storage>UFS</storage>
<ufs>
    <block_size>0x1000</block_size>
    <lua0_size>0x400000</lua0_size>        <!--   4 MiB -->
    <lua1_size>0x400000</lua1_size>        <!--   4 MiB -->
    <lua2_size>0xee5800000</lua2_size>     <!--  59 GiB -->
    <lua3_size>0</lua3_size>
```

**两个 4MiB + 一个 59GiB** —— 这正是 UFS boot 布局的形状
（boot LUN 小，用户区巨大）。

所以：**`LUA0`/`LUA1` 是 boot LUN，`LUA2` 是用户区。**

**结论**：**`LUA<n>` 和 `lu<n>` 的编号不是一回事** —— 在 MTK 的 LUA 编号里两个 boot LUN 排在前面。

按这个理解：
- **命名分支是对的**（`user`→59GiB→`LUA2`，`boot1`→4MiB→`LUA0`，`boot2`→4MiB→`LUA1`）
- **`lu*` 分支是错的**（`lu2`→`LUA2` 会让第二个 boot LUN 变成 59GiB）

### B. `boot2` 的 size 配对不一致

`storage.py:305-311`：

```python
elif parttype == "boot2":
    if not xml:
        parttype = UFSPartitionType.BOOT2
        self.flashsize = self.ufs.lu2_size
    else:
        parttype = "UFS-LUA1"
        self.flashsize = self.ufs.lu0_size    # ← 只有这一处分组不一致
```

其他 xml 分支都是 `LUA<n>` 配 `lu<n>_size`：

| 行号 | xml 值 | flashsize |
|---|---|---|
| 296-297 | `UFS-LUA2` | `lu2_size` |
| 303-304 | `UFS-LUA0` | `lu0_size` |
| 310-311 | `UFS-LUA1` | **`lu0_size`** ← 唯一不匹配 |
| 317-318 | `UFS-LUA3` | `lu3_size` |

MTK 的样本里两个 boot LUN 恰好都是 4MiB，所以观察不到差异 ——
但配对本身不自洽，像是复制粘贴遗留。

### C. `lu0`→`UFS-LUA0` 是死代码

`storage.py:322-324`：

```python
if parttype == "lu0":
    if xml:
        parttype = "UFS-LUA0"    # ← 永不执行
```

**xml 路径下**，`parttype` 在 293-318 行已经变成 `"UFS-LUA*"` 字符串了，
所以 322 行的字符串比较失败 → 323-337 整段不可达。
（非 xml 路径下是整数比较失败 —— 就是 PR #388 修的那个）

---

## 一处不算 bug 的代码一致性问题

### `return []` 与调用者的返回值约定不一致

错误路径 `return []`，而正常路径返回 3 元组 `(storage, parttype, length)`。

不过**实测三处调用点都有守卫**，所以不会崩：
```python
# xflash_lib.py:711 / 717   readflash
# xflash_lib.py:851         writeflash
# xflash_lib.py:351         formatflash
if not partinfo:
    return False
```
所以这条**不是**实际缺陷，只是代码一致性问题，没有单独提 issue。
