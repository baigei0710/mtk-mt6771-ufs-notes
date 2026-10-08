#!/bin/bash
# 补充备份: para / md1img_a (基带) + 从 flash 抠出原厂 preloader
# 带外部硬超时, 不会卡死终端
#
# 用法:  ./grab_more.sh [等待秒数]    默认 300

WAIT=${1:-300}
cd "$HOME/tablet_dump" || exit 1

echo ">>> 等待 ${WAIT} 秒, 现在插线 <<<"
perl -e "alarm $WAIT; exec @ARGV" /opt/homebrew/bin/python3.14 grab_more.py
RC=$?
echo
echo "退出码: $RC  (142=超时, 0=正常完成)"
