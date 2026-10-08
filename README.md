# MT6771 (Helio P60) UFS 平板折腾记录

一次尝试给 MT6771 + UFS 平板刷系统 / 拿 root 的完整记录。**结论是失败**，但过程中挖出了不少
MTK 平台的底层机制，以及一个 mtkclient 的真实 bug（已提 PR）。

设备是一台定制学习机（型号 `PF135`，设备树 `tb8788p1_64_bsp`），管控很严，最终没能拿到 root。

---

## 快速结论

**这台设备在管控层面是设计性封闭的** —— 想要 root，缺的不是写权限，而是**执行通道**：

| 入口 | 状态 |
|---|---|
| bootloader 锁 | 🔓 已解锁（`SLA`/`SBC`/`DAA` 全 False） |
| MTK DA（底层读写） | ✅ 完全可用（能读写任意分区） |
| fastboot | ❌ bootloader 不实现 |
| adb | ❌ 系统禁用 + 设置锁死 |
| MTP | ❌ 无接口 |
| 浏览器输入网址 | ❌ 被禁止 |
| recovery USB | ❌ 被 `cmode 2`（HOST_ONLY）废掉 |

**关键认知**：能写 flash ≠ 能控制系统。

---

## 我们做了什么

按时间顺序：

1. **摸清存储布局** —— 通过 mtkclient 的 DA（Download Agent）读出 GPT，确认：
   - 存储是 UFS 2.2（Micron），不是 eMMC
   - 分区是 Android 11 A/B + 动态分区（`super` 6GB）
   - 共 49~53 个分区

2. **找到并提取原厂 preloader** —— UFS 的 preloader **不在 LU0**，在 **LU1**（UFS 专用 boot LUN）。
   为此修复了 mtkclient 的 UFS `--parttype` bug（见下）。

3. **完整备份关键分区** —— 16 个文件 / 395MB，包含：
   - 原厂 preloader（273KB，含 `UFS_BOOT` 标记）
   - `nvram`/`nvdata`/`nvcfg`/`proinfo`/`persist`（IMEI、射频校准、传感器校准）
   - `boot`/`lk`/`tee`/`gz`/`vbmeta`/`dtbo` 等引导链

4. **尝试改 boot 镜像拿 root** —— 解包 ramdisk（gzip + cpio），修改属性后重打包写回。
   - ✅ 第一次（改 `prop.default`）**成功引导**，证明改动链条安全
   - ❌ 后续几次（改 `init.recovery.*.rc`）全部 **bootloop**

5. **用 BCB 强制进入 recovery** —— 往 `para` 分区偏移 `0x00` 写 `boot-recovery`，
   实测**能成功进入 Android Recovery**。

6. **定位 recovery USB 失效的根因** —— 读取 MTK 的 `expdb` 崩溃日志分区拿到内核日志，
   发现 `musb_cmode_store NORMAL --> HOST_ONLY`。

7. **发现并修复 mtkclient 的 UFS parttype bug** —— 并提交了 PR。

8. **最终失败** —— recovery 的 USB 不枚举；`init.rc` 的修改反复导致 bootloop；
   没有 adb 就无法继续。

---

## 文档

| 文件 | 内容 |
|---|---|
| [`docs/problems.md`](docs/problems.md) | **29 个问题**按性质分类（环境 / mtkclient bug / 设备机制 / 我的误判 / 我造成的损失） |
| [`docs/mtkclient-bugs.md`](docs/mtkclient-bugs.md) | mtkclient 的 4 个缺陷（1 个已提 PR，#388） |
| [`docs/ufs-boot-layout.md`](docs/ufs-boot-layout.md) | UFS 启动布局：preloader 为什么在 LU1 |
| [`docs/recovery-boot.md`](docs/recovery-boot.md) | MTK 进 recovery 的三种机制 + BCB 写入方法 |
| [`docs/device-info.md`](docs/device-info.md) | 设备档案（已脱敏） |

## 修改记录

| 补丁 | 说明 | 结果 |
|---|---|---|
| [`patches/fix-ufs-parttype.patch`](patches/fix-ufs-parttype.patch) | 修 mtkclient 的 UFS parttype bug | ✅ **已提 PR #388** |
| [`patches/01-boot-prop-default.md`](patches/01-boot-prop-default.md) | 改 boot ramdisk 的 `prop.default` | ✅ 能引导，但属性被 system 覆盖 |
| [`patches/02-recovery-cmode.md`](patches/02-recovery-cmode.md) | 改 recovery 的 `cmode` 值 | ❌ bootloop |

## 工具

`tools/` 下是从这次折腾中沉淀的可复用脚本（抓取 / 分析 / patch / 回退）。

---

## 最重要的几条教训

1. **判断设备能否 root，先看"通道"而非"权限"**
2. **分区分级**：`boot`/`vbmeta` 可改 · **`preloader` 绝对不能碰** · IMEI/校准数据跨机绝对不行
3. **不要在看不到设备内部状态的情况下盲改 `init.rc`**（我因此造成 3 次 bootloop）
4. **`expdb` 分区是 MTK 排障利器**（含 preloader + 内核 + ramoops 日志）
5. **硬超时必须外挂**：`perl -e 'alarm N; exec @ARGV'`（Python 的 SIGALRM 对阻塞 USB 调用无效）

---

## 致谢

- [mtkclient](https://github.com/bkerler/mtkclient) —— 整个探索的基础工具
- 顺便在这台设备上发现并修复了它的一个 UFS parttype bug
