# Windows 第三方通知、对应源码和替换说明

ChordCue 自身、原创图标和演示和弦采用项目 MIT。第三方库仍按各自许可证提供，不因装入 ChordCue MSI 而改为 MIT。算法参考关系另见根目录 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。

## 分发材料

Windows 构建使用 PySide6 / Shiboken6 6.11.2 和 Qt 6.11.2。开发环境实际运行时查询到 Qt WebEngine 6.11.2、Chromium 140.0.7339.225；最终安装包应再次核对。公开 wheel 的许可证元数据与仅有商业许可文本的附带文件不能相互替代，因此本仓库另外保存开源许可和官方第三方通知。

| 文件／位置 | 用途 |
| --- | --- |
| `licenses/Qt-LGPL-3.0.txt`、`Qt-GPL-3.0.txt` | 从 Qt 6.11.2 对应标签取得的完整 LGPL v3 / GPL v3 文本 |
| `licenses/GNU-LGPL-2.1.txt`、`GNU-LGPL-2.0.txt`、`GNU-GPL-2.0.txt` | GNU 官方完整文本，供 Chromium / WebKit / FFmpeg 等归属说明引用；不是只有网页链接 |
| `licenses/Qt-6.11.2-THIRD-PARTY-NOTICES.txt` | Qt 官方归属页面的离线完整正文，包括版权、条件、免责声明和相关说明 |
| `licenses/Qt-6.11.2-notices-manifest.json` | 每页 URL、版本证据、原始 HTML SHA-256、提取文本 SHA-256，以及汇编和补充全文哈希 |
| `licenses/PySide6-6.11.2-NOTICES.txt` | 官方同版本源码 ZIP 中 27 个许可／归属文件，包括 Shiboken 借用的 Python 3.7 代码通知及每个归属记录指定的完整 LicenseFile |
| `licenses/Qt-PySide-SOURCE-INFORMATION.txt` | 供安装后的离线用户阅读的对应源码入口和动态库替换／重新构建说明 |
| 安装包 `Resources/licenses/installed-distributions.json` | 构建环境中实际收集的 Python 包名称、版本和许可证文件路径 |
| 安装包 `Resources/licenses/` | 项目许可、Python 许可、运行包许可、cx_Freeze 启动部分／freeze-core 许可及以上离线汇编 |
| 安装包 `share/licenses/vc_redist/` | 应用本地 VC 运行库的分发条款 |

Qt 汇编采用**所有模块和平台的官方文档超集**，其中会包含 ChordCue Windows 没有编译或使用的组件。这样保留归属信息并不声称这些组件全部进入了二进制，也不将其视为最终 SBOM。Qt 某些通用归属页只显示 6.11 的次版本；脚本对这些页面记录“补丁版本来自已验证的 6.11.2 总索引”，不会伪称页面自身标出了补丁号。

Qt 汇编不删减归属页的许可证正文，包含 281 页，其中 Qt WebEngine 126 页；所有页面均提取到了预格式化的许可正文。HTML 表现标记与转义被移除，页面按 URL 排序。PySide 汇编直接读取 6.11.2 官方源码归档，并包括其中的 `LICENSES` 目录、COPYING、归属记录及记录指定的许可文件；它同样包含示例材料这一超集。源码 ZIP 的 SHA-256 为 `c0fdd62b91a1d36d5ee2e1fb71050a32fbc93fcdeef0fdcb41d29afaaf00d9b5`，完整记录见 manifest。

原始网页和 PySide 源码归档只缓存于仓库外的 `../qt-license-cache`。已检查无网络重建得到相同汇编内容。构建脚本会将根目录 `licenses/` 文件带入程序资源；许可证材料生成不依赖运行时的 `chrome://credits` 页面。

来源：[Qt 6 第三方归属索引](https://doc.qt.io/qt-6/licenses-used-in-qt.html)、[Qt WebEngine 许可](https://doc.qt.io/qt-6/qtwebengine-licensing.html)、[Qt for Python 许可](https://doc.qt.io/qtforpython-6/licenses.html)。

## 对应源码获取

应用源码、冻结和 MSI 配置在 [ChordCue 仓库](https://github.com/Gonghysin/ChordCue) 的相应发行 commit。Qt / PySide 的上游 6.11.2 源码入口与本轮已核对的官方文件名如下：

- [Qt 6.11.2 完整源码 ZIP](https://download.qt.io/official_releases/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.zip)
- [Qt Base 6.11.2 源码 ZIP](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.zip)
- [Qt WebEngine 6.11.2 源码 ZIP](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtwebengine-everywhere-src-6.11.2.zip)，包含对应 Chromium 来源树
- [Qt WebChannel 6.11.2 源码 ZIP](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtwebchannel-everywhere-src-6.11.2.zip)
- [Qt Declarative 6.11.2 源码 ZIP](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtdeclarative-everywhere-src-6.11.2.zip)
- [PySide / Shiboken 6.11.2 完整源码 ZIP](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.zip)

同目录提供 `.tar.xz` 版本及镜像／校验信息。构建说明见 [Qt for Python 源码构建](https://doc.qt.io/qtforpython-6/building_from_source/index.html)；ChordCue 的 Python 版本、锁文件与冻结步骤见 [BUILD.md](BUILD.md)。

本项目当前使用发布的 wheel，没有修改 Qt 或 PySide 的源码。这些上游版本链接用于定位源码，不代表已经证明重新构建与 wheel 按字节一致。发行者仍须保存并核对实际分发版本的对应源码、任何补丁及构建配置；若选择以书面源码要约履行适用义务，必须另行给出确实可履行的联系方式、范围和期限，不能把本段上游链接称作已经履行的书面要约。最终发行审查应确认用户能够取得相应材料。

## 动态库替换与重新构建

冻结程序把 Qt 和 PySide 的 DLL、Python 扩展、插件、WebEngine 进程和资源作为独立文件分发，未将 Qt 静态链接进 ChordCue 自己的代码。本项目不添加阻止用户为调试其修改版库而进行逆向工程、替换动态库或重新构建的附加限制；各组件许可证本身仍适用。

建议先在个人可写目录复制完整冻结目录，保留原始安装供对照。使用匹配 x64 架构、Qt 6.11 / PySide ABI 和构建配置的修改版本，成套替换冻结目录中的 `lib/PySide6`、相关 `shiboken6` 文件、Qt 插件、QtWebEngineProcess 和 WebEngine 数据／locales；单独替换一个不匹配的 DLL 可能无法加载。更可靠的操作是将兼容的自行构建 wheel 安装进独立开发环境，安装 ChordCue 源码后重新运行冻结构建与 smoke。路径以最终载荷为准。

Program Files 的 Windows 权限与 MSI 修复可能影响手工替换，因此开发副本应放在个人可写目录。应用不要求将修改后的库上传给维护者。上述说明提供工程操作入口，不宣称所有许可证义务已完成；最终安装包及对应源码的逐项审查仍属于发布门槛。
