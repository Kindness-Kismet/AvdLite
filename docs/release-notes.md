两个镜像均通过本次 Actions 的冷启动、重启和设置持久化验证。

- `AvdLiteWithKsu-Android<版本>-api<API>.zip`：包含构建时的官方最新稳定 KernelSU，匹配原内核 KMI。
- `AvdLiteWithoutKsu-Android<版本>-api<API>.zip`：标准精简镜像。

镜像和依赖来自官方远程源。ZIP 内包含系统镜像、2 核 / 4096 MB / 32 GB / Resizable AVD 配置、导入脚本、文件 SHA-256 清单及构建验证报告。Release 资产摘要可用于校验 ZIP。

解压后使用 Python 3.11 或更新版本执行：

```text
python import-avd.py --sdk <Android SDK 目录>
```

默认 AVD 名为 `AvdLiteWithKsu` 或 `AvdLiteWithoutKsu`。可通过 `--avd-home <AVD 目录>` 指定模拟器数据位置，通过 `--name <名称>` 创建独立实例。版本、实际精简内容和验证结果详见包内 JSON 报告。
