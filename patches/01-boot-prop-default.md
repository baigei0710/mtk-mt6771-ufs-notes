# 修改记录 01：boot ramdisk 的 `prop.default`

> **结果**：✅ 能正常引导，但**属性没生效**（被 `/system/build.prop` 覆盖）

这是第一个尝试 —— 想通过改 boot 镜像的 ramdisk 来开启 root 相关属性。

---

## 背景

思路：`boot` 分区里有 ramdisk，ramdisk 里有 `prop.default`。
把 `ro.debuggable` 改成 1 应该就能让 adb 以 root 运行。

设备状态：
```
Target config: 0x0
  SBC / SLA / DAA 全 False       ← 无签名校验
  bootloader 已解锁
```
内存里已有 VFS 结构：`boot` 的 VMETA flags = normal。

---

## 做了什么

### 1. 解包 boot 镜像
```
magic         : "ANDROID!"   (boot v2)
page_size     : 2048
kernel_size   : 9671351
ramdisk_size  : 9226204
ramdisk 偏移  : 0x93a000
```

### 2. 解包 ramdisk
```
gzip 解压        → 21,123,328 字节
cpio 格式        → newc ("070701" 魔数)
cpio 条目数      → 444
```

### 3. 修改 `prop.default`
```diff
-ro.debuggable=0
+ro.debuggable=1

-ro.secure=1
+ro.secure=0

-ro.adb.secure=1
+ro.adb.secure=0
```

### 4. 重新打包 + 写回
- cpio 重打包 → gzip → 重建 boot 镜像
- 用 DA 按偏移写入 `boot_a`（`0x20a00000`）
- 同时写 `vbmeta_a` 关闭校验（`0x78` 处 flags = 3 = verity + verification 都关）

---

## 关键：ramdisk 重建的正确做法

**踩过的坑**：

### a) cpio 打包的填充计算
```python
out += nb
# ✗ 错误：用到的是写之前的长度（变量遮蔽陷阱 —— 和 mtkclient 那个 bug 同类）
out += b"\x00" * (align(len(out) + len(nb), 4) - (len(out) + len(nb)))
```
正确写法：
```python
out += nb
pad_n = align(len(out), 4) - len(out)     # ★ 写完再取长度
if pad_n:
    out += b"\x00" * pad_n
```

### b) TRAILER 条目的 mode 不能改
原来想规范化 TRAILER 的 mode，结果往返不一致。**原样保留就对了。**

### c) boot header 要整块复制，只改需要改的字段
```python
hdr = bytearray(orig_boot[:page_size])    # ★ 整块复制
struct.pack_into("<I", hdr, 0x10, len(new_ramdisk))   # 只改 ramdisk_size
```
**不要自己重建 header** —— v2 header 的 `extra_cmdline` 从 `0x608` 开始
（不是 608！），自己重建会误清零后面的字段。

### d) AVB footer 必须保持在分区末尾的固定位置
镜像里有 AVB 结构（`AVB0` 在 `0x1222000`，`AVBf` 在 `0x1ffffc0`）。
ramdisk 大小变化会导致它们整体位移 → 需要在"内容"与"AVB tail"之间补齐：

```python
orig_content_end = align(roff + rsize, page)   # 原镜像内容结束位置
tail = raw_boot[orig_content_end:]             # 原 AVB 结构

out = header + kernel + pad + new_ramdisk + pad
if len(out) < orig_content_end:
    out += b"\x00" * (orig_content_end - len(out))   # ★ 在这里补齐
out += tail                                       # AVB 位置不变
if len(out) < len(raw_boot):
    out += b"\x00" * (len(raw_boot) - len(out))     # 补齐到原总长
```

---

## 结果

### ✅ 成功
- **镜像结构完全正确**：
  ```
  大小一致        : ✓ (33554432)
  AVB0 位置不变   : ✓ (0x1222000)
  AVBf 位置不变   : ✓ (0x1ffffc0)
  kernel 区一致   : ✓
  AVB tail 区一致 : ✓
  header 差异     : 仅 2 字节 (ramdisk_size)
  ```
- **设备能正常引导** —— 证明整条改动链条是安全的
- （中途出现过"卡重启"，但那是 Android 首次启动的正常重启，之后正常进系统）

### ❌ 但属性没生效
从系统里读出来：
```
ro.debuggable = 0     ← 我们改成了 1，但系统里还是 0
ro.secure     = 1
```

**原因**：boot ramdisk 是 **first-stage ramdisk**，
Android 属性加载的优先级是：
```
prop.default (first-stage ramdisk)   ← 优先级最低
    ↓ 被覆盖
/system/build.prop                   ← 优先级更高
/vendor/build.prop
/odm/etc/build.prop
```

**而且更根本的问题**：这台设备的 USB 调试**从来就没开过**，
所以 `adbd` 根本不会启动 —— 改属性即使生效也没有 adbd 去读它。

---

## 教训

1. **"镜像结构正确" ≠ "改动会生效"** —— 还要确认目标在启动流程里的优先级
2. **改 boot ramdisk 的属性优先级最低**，要改属性应该改 `system` 分区
3. **动手前先确认前提**：这台设备有没有 adb 可用？（我们后来才发现从来没有）
4. ramdisk 是标准格式（gzip + cpio newc），纯 Python 就能完整处理，不需要外部工具
