精简系统镜像与 AVD 配置已通过本次 Actions 的冷启动、重启和设置持久化验证。

- `ksu.zip`：包含构建时的官方最新稳定 KernelSU，匹配原内核 KMI。
- `plain.zip`：标准精简镜像。
- `.sha256`：对应 ZIP 的 SHA-256。

ZIP 内包含系统镜像、2 核 / 4096 MB / 32 GB / Resizable AVD 配置、导入脚本和构建验证报告。

解压后使用 Python 3.11 或更新版本执行：

```text
python import-avd.py --sdk <Android SDK 目录>
```

可通过 `--avd-home <AVD 目录>` 指定模拟器数据位置。版本、实际精简内容和验证结果详见包内 JSON 报告。
