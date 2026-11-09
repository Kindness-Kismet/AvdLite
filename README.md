# AvdLite

为 Android 15（API 35）起的官方 Google APIs x86_64 模拟器镜像提供可重复的精简构建。

当前已接入官方版本解析、摘要校验、Actions 下载缓存和镜像分区准备检查。镜像修改、硬件条件启动和可选 KernelSU 注入正在实现；准备检查产物不是可启动的精简镜像。

## 当前工作流

在 Actions → AvdLite → Run workflow 中选择：

- `api`：`latest` 或 API 35 起的版本，例如 `35`、`36.1`、`37.2`。不是 Android 产品版本号。
- `page_size`：`auto`、`4k` 或 `16k`。`auto` 选择最新稳定 API，并在同版本优先 16 KB。
- `enable_ksu`：解析并下载官方最新稳定 KernelSU release，按资产摘要缓存。

Beta、Canary、废弃镜像不会进入默认候选。指定版本不存在时明确失败，不替换为其他版本。

Google 归档使用官方索引的摘要验证，KernelSU 使用官方 release 的 SHA-256。即使命中缓存，也重新验证文件摘要。缓存和临时产物均不进入 Git，`.gitignore` 默认忽略所有内容，仅允许明确列出的源码与文档。

## 构建目标

- 精简可选应用，保留 GMS、WebView、输入法、桌面及系统依赖。
- 解除主题与旋转设置限制，保留上游默认语言、时区。
- 状态栏边距 12 dp，默认不修改屏幕圆角。
- 无模拟器端口时不宣告 UWB / NFC 硬件，避免持续等待或崩溃；保留组件以供有后端时使用。
- 可选最新 KernelSU，保留原内核并核对 KMI，不自动降级到旧 release。
- 检查文件系统与 AVB，输出独立镜像，不使用用户数据盘作为构建来源。
