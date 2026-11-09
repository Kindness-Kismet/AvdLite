# AvdLite

基于 Android 15（API 35）起的官方 Google APIs x86_64 镜像，构建精简版 Android 模拟器系统。

## 构建内容

- 精简可选应用，保留 GMS、WebView、输入法、桌面及系统依赖。
- 解除主题与旋转设置限制，保留上游语言、时区默认值。
- 状态栏左右边距 12 dp。
- 根据模拟器端口启用 UWB / NFC：有端口时启动服务，缺少端口时隐藏功能声明。
- 可选 KernelSU，使用官方最新稳定 release 和匹配内核 KMI 的模块。
- 检查文件系统、重建分区布局并签名 AVB，输出独立系统镜像。

## 运行工作流

打开 **Actions → AvdLite → Run workflow**，选择构建参数：

| 参数 | 用途 |
| --- | --- |
| `api` | Android API 级别，支持 `latest` 或 `35` 起的数字，如 `35`、`36.1`、`37.2` |
| `page_size` | `auto`、`4k`、`16k`；`auto` 在最新稳定 API 中优先选择 16 KB |
| `enable_ksu` | 启用 KernelSU，构建时解析最新稳定 release |

镜像来源限定为官方稳定通道。指定版本或 KMI 模块缺失时，工作流会报告具体原因并停止该项构建。

自动测试使用最新稳定镜像，并行检查 KernelSU 开启和关闭两种配置。运行结果和构建产物见 [Actions](https://github.com/Kindness-Kismet/AvdLite/actions)。

## 获取和导入

构建通过后，ZIP 与 SHA-256 文件自动发布到 [Release Assets](https://github.com/Kindness-Kismet/AvdLite/releases)。`ksu` 包含 KernelSU，`plain` 为标准精简版。

解压后使用 Python 3.11 或更新版本导入：

```text
python import-avd.py --sdk <Android SDK 目录> --avd-home <AVD 数据目录>
```

导入器校验摘要、保存系统镜像并注册独立 AVD。默认配置为 2 核、4096 MB 内存、32 GB 存储、Resizable 和 Quick Boot；`--name` 可指定名称。Android SDK 需安装当前稳定版 Emulator。

## 下载与缓存

Google 镜像使用官方索引摘要验证，KernelSU 资产使用官方 SHA-256。缓存键包含内容摘要，每次恢复缓存后重新校验。

`.cache/` 保存下载内容，`.work/` 保存本地研究和临时产物。Git 使用白名单管理源码、资源和文档；每次推送前执行隐私检查。

## 精简规则

[精简清单](resources/debloat.json) 按包名列出移除对象、原因和保留依赖。构建报告记录实际移除内容，以及当前版本缺少的候选包。

硬件组件保持预装。UWB 服务通过端口属性触发并单次运行；init 在框架扫描前挂载与端口状态对应的功能声明，覆盖 vendor 和 NFC APEX。

## 验证范围

构建阶段检查官方摘要、GPT/动态分区元数据、AVB 公钥与签名、ext4 文件系统，以及原内核和上游 `build.prop` 的保留情况。

启动阶段使用独立 AVD，检查核心应用、SELinux Enforcing、硬件声明、资源覆盖、KernelSU 初始化，并重启验证深色主题定时设置。每次运行保存日志、截图和 JSON 报告。
