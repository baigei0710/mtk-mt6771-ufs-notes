#!/bin/bash
DUR=${1:-420}
LOG=$HOME/tablet_dump/usb_monitor2.log
: > "$LOG"
echo "监听 ${DUR} 秒（不排除任何设备）" 
end=$((SECONDS+DUR)); prev=""; adbprev=""
while [ $SECONDS -lt $end ]; do
  cur=$(ioreg -p IOUSB -w0 2>/dev/null | grep -oE '\+-o [A-Za-z0-9_.@-]+' | grep -viE "hub|XHCI|root" | tr '\n' '|')
  if [ "$cur" != "$prev" ]; then
    echo "[$(date +%H:%M:%S)] USB: ${cur:-（无）}" | tee -a "$LOG"
    prev="$cur"
    ioreg -p IOUSB -w0 -l 2>/dev/null | grep -iE "kUSBSerialNumberString|idVendor\"|idProduct\"" | sort -u | head -6 | sed 's/^ *//' | tee -a "$LOG"
  fi
  ad=$(adb devices 2>/dev/null | grep -vE "List of devices|^$" | tr '\n' '|')
  if [ -n "$ad" ] && [ "$ad" != "$adbprev" ]; then
    echo "[$(date +%H:%M:%S)] ★★★ adb: $ad" | tee -a "$LOG"; adbprev="$ad"
  fi
  sleep 1
done
echo "监听结束"; adb devices | tee -a "$LOG"
