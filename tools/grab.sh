#!/bin/bash
# MT6771 UFS 平板 - 抓取关键数据 (带外部硬超时, 不会卡死终端)
#
# 用法:  ./grab.sh [等待秒数]       默认 180 秒
#
# 启动后脚本进入等待, 你再去插 USB / 上电.
# 超时或完成后自动退出, 任何时刻 ctrl+C 也能中断.

WAIT=${1:-180}
cd "$HOME/tablet_dump" || exit 1

echo ">>> 等待 ${WAIT} 秒, 现在插线 <<<"
# perl 的 alarm 作为外部硬超时: 阻塞式 USB 调用无法被 Python 自身中断
perl -e "alarm $WAIT; exec @ARGV" /opt/homebrew/bin/python3.14 grab.py "$WAIT"
RC=$?
echo
echo "退出码: $RC  (142=SIGALRM超时, 0=正常完成)"
echo "备份目录: $HOME/tablet_dump/backup"
ls -l "$HOME/tablet_dump/backup" 2>/dev/null | tail -20
