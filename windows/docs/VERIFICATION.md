# 验证记录与发布门槛

记录日期：2026-10-01。用户确认目前没有额外实机资源，保留候选安装包并列出待验收项，暂不创建 PR。状态只描述已有证据；生成 MSI 不等于安装生命周期已通过。

候选版本：`0.2.0`，文件 `ChordCue-0.2.0-win-x64.msi`，141545472 字节，未签名。
本机候选 SHA-256：`0fd80c9b0ac5c75966d2060f3f9d7452ef4386b1063626b4fb33215121c69510`。
应用实现提交 `cb21f4e`，打包配置 `e63597e`；最终集成检查使用 `ca46815`（包含诊断脚本路径修正及真实浏览器测试，应用载荷未改变）。不同机器重新构建的 MSI 不保证字节相同，CI 产物附有各自的 SHA-256。

Feature Issue：[Gonghysin/ChordCue#2](https://github.com/Gonghysin/ChordCue/issues/2)。
代码分支：[JoeyZhuoer/ChordCue — codex/windows-desktop](https://github.com/JoeyZhuoer/ChordCue/tree/codex/windows-desktop)。
自动化记录：[Build and test — ca46815](https://github.com/JoeyZhuoer/ChordCue/actions/runs/36850683918)，Windows 和 macOS 两个任务均成功。

## 已取得的局部证据

| 检查 | 状态与范围 |
| --- | --- |
| 完整本地测试（带最终冻结目录与 MSI） | PASS：348 passed、6 skipped；6 项跳过是本地没有 Swift 的对照用例，macOS CI 另行实际执行。覆盖乐理、文件、时钟、音频、UI、PDF、LAN、平台事件与安装包。 |
| 浏览器音频调度 | PASS：11 项 Node 测试执行实际共享 JavaScript，并用假时钟和 AudioContext 检查真实排程／取消调用；涵盖 400 ms 首拍准备、恢复、100 次循环、多循环前瞻、结束边界、350 ms 过期、revision／session／手动取消、旧版数据和游标预测。 |
| 原生音频桥接 | PASS：7 项真实 Qt 页面测试，加 1 项 Node pytest 包装测试；验证页面加载、时钟握手、revision、隐藏后 Active、访问限制、停机及偏好重载。未启用实体扬声器输出。 |
| Python 静态检查 | PASS：全部源码、测试、打包和脚本通过 Ruff；15 个应用源码文件通过 mypy。 |
| 独立审查 | PASS（源码范围）：5 项复现缺陷和 1 项源码风险均已修复；独立 8 项回归用例通过。详见 [INDEPENDENT_QA.md](INDEPENDENT_QA.md)。 |
| 完整程序源码运行 smoke | PASS：开发主机 Windows build 19045、Python 3.13.15、Qt 6.11.2；窗口、OfflineAudioContext 和 PDF 生成通过。该主机不是无 Python 的干净验收机；PDF 生成通过也不代表所有密集谱面视觉验收通过。 |
| LAN 协议及实际浏览器 | PASS（本机）：66 项协议检查；两个真实 QWebEngine 页面验证 SSE、时钟、分数拍、曲长、独立个人设置及关闭断线。不能代替第二台物理设备。 |
| 最终冻结程序 | PASS：实际 GUI 可执行文件启动、WebEngine 时钟桥、离线合成、PDF、截图；PATH 仅保留 Windows System32 时通过，输出路径含空格时通过。 |
| 最终 MSI 与载荷 | PASS（独立静态审查）：实际 MSI 表与保存记录一致，412 个文件的路径和大小与冻结目录一致；全用户 x64、Program Files、升级/降级条件及动作顺序、开始菜单/图标、无 PATH/防火墙变更、必要 DLL/资源/许可及许可哈希通过。构建成功不代表实际安装、升级和回滚已通过。 |
| Windows CI | PASS：锁定依赖安装、Ruff/mypy、Python/JS 测试、独立 MSI 构建、最终表与载荷检查、实际冻结 GUI/WebEngine/PDF smoke 及产物上传均通过。托管 Windows Server 2022 不能代替消费版干净实机。 |
| UI / PDF 可视检查 | PASS（开发主机）：Qt 有效缩放 100%、150%、200% 的冻结程序窗口；1/4、7/4、12/4、分数拍、中文升降号、密集和弦与跨页样例已渲染检查。普通谱面 28 小节/页；密集行增高，必要时续页。 |
| macOS 原项目构建与 Swift 对照 | PASS：原 macOS 构建、真实 Swift oracle 编译及 7 项对照/样例检查在 macOS CI 通过。未进行 Logic Pro 或 WKWebView 的实体音频验收。 |
| Qt / Chromium / PySide 离线通知 | PASS（材料生成）：281 个 Qt 官方归属页，其中 Qt WebEngine 126 页；另从 PySide 6.11.2 官方源码 ZIP 提取 27 个许可／归属文件及其引用的完整 LicenseFile。Qt 汇编 SHA-256 为 `6168748aa1e24bdfbfd237c9c1d9e3fcca46075d47fb8cd6a0a94a222b2faa11`。无网络重建得到相同内容；最终包和源码义务审查仍单列。 |

自动化音频测试证明的是排程逻辑。OfflineAudioContext 证明离线音频 API 可运行，两者都不证明扬声器时间准确、设备切换可靠或真实浏览器声学同步。

## 仍阻止发布的真实环境项目

| 验收项 | 当前状态 | 需要保留的证据 |
| --- | --- | --- |
| 干净 Windows 10 22H2 x64，无 Python | BLOCKED：本轮尚无对应消费版测试机证据 | OS build、安装／首次启动／非管理员使用、中文和空格用户路径、完整应用 smoke、PDF 与 LAN 记录 |
| 干净 Windows 11 x64，无 Python | BLOCKED：本轮尚无对应消费版测试机证据 | 同上；不能用 Windows Server CI 代替 |
| MSI 首装、普通用户及第二账户运行、升级、失败升级回滚、修复、降级阻断、卸载 | BLOCKED：需要隔离测试机执行生命周期测试 | 两个不同版本 MSI 的哈希、verbose MSI 日志、结果 JSON、账户间配置隔离及卸载后用户数据保留证据 |
| 第二台实体局域网设备 | BLOCKED：未完成双设备验证 | 主机和客户端系统／浏览器、投放开关、谱面先于位置、循环与停止、失联停声、重连、个人显示设置 |
| 实体音频：最小化持续 10 分钟 | BLOCKED：未完成耳机／声卡实测 | 设备、60/120/240 BPM、四分/八分、重音、经典/鼓机音色、循环设置、操作时间和录音／可复查观察记录；检查漏拍、重拍、漂移 |
| 实体音频：切换标签持续 10 分钟 | BLOCKED：未完成实测 | 同上，另记录两个标签间操作及桌面焦点变化 |
| 锁屏／睡眠／唤醒及输出设备变化 | BLOCKED：未完成实体流程 | 暂停和关闭声音发生时机、解锁不自动发声、重新开启行为及设备切换结果 |
| 最终许可与对应源码审查 | 通知文件和哈希已进入最终包；发行前对应源码义务复核仍 PENDING | 对应版本源码获取信息、构建配置及分发包清单；不能将上游链接称作已经履行的书面要约 |

不得把 BLOCKED 改写为通过，也不得以源码中的处理函数替代真实系统事件验收。所有发布门槛关闭前，不创建发布 PR。可以保存开发产物和测试记录，但应明确其候选构建身份。

## 建议验收顺序

1. 固定待验收 commit、版本号、锁文件和最终 MSI 哈希，运行完整测试、静态检查、macOS 构建和 Swift 对照。
2. 构建最终 MSI，检查 MSI 表和完整冻结程序的 smoke；确认许可证和 WebEngine 文件进入实际载荷。
3. 在隔离的 Win10 / Win11 系统完成安装、更新、故障回滚、修复、降级阻断、卸载和用户数据保留。不要在含有用户现有安装的机器上运行破坏性生命周期用例。
4. 完成第二设备 LAN、两项各 10 分钟实体音频、锁屏／睡眠／唤醒和输出设备变化验证。
5. 把结果、限制和失败项写入具体候选构建记录；全部门槛满足后再进行发布审查和 PR。

每份结果至少包含日期、测试人、系统 build、应用版本／commit、安装包哈希、操作步骤、实际结果和日志路径。签名证书状态、声学同步测量与主观听感应分别记录。
