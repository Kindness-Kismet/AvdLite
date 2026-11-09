#!/system/bin/sh
set -eu

# 管理器须安装到数据区，KernelSU 才能识别它；后续启动检查安装状态。
BUNDLED=/product/app/KernelSU/lib/x86_64/libksud.so
if [ ! -x /data/adb/ksud ]; then
    "$BUNDLED" install
fi
case "$(pm path me.weishu.kernelsu)" in
    package:/data/app/*) ;;
    *) pm install -r /product/app/KernelSU/KernelSU.apk ;;
esac
/data/adb/ksud debug version
setprop sys.avdlite.ksu_ready 1
