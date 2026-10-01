# 第三方来源、参考与许可证

ChordCue 自身采用 MIT。下表区分运行依赖、算法／结构参考与仅调研项目；参考项目的作者及许可不因本项目 MIT 而被替代。

核对日期：2026-09-29。下表主要记录原 macOS 实现的参考关系；macOS 系统框架由操作系统提供。Windows 安装包另外分发 Python、PySide6 / Qt 和冻结运行时，具体说明见后文，不能用“参考项目未打包”概括 Windows 的实际依赖。

| 来源 | 使用关系 | 许可证与归档 |
| --- | --- | --- |
| [Chris Wilson / cwilso/metronome](https://github.com/cwilso/metronome) | Resources/Metronome.js 参考“下一音符游标＋提前排程”结构，自行适配 Logic 时间戳、断流与输出时钟；不是完整库移植。保留 MIT 通知，覆盖此参考关系。 | MIT；[原文](licenses/metronome.txt)；[核对提交](https://github.com/cwilso/metronome/tree/28a6e49d9dd75985d67d94fa9f45327d7310d62f) |
| [Aaron Bull Schaefer and contributors / WhatKey (whatchord)](https://github.com/EarthmanMuons/whatchord/tree/main/packages/whatkey) | ChordTheory.swift 参考调性状态持续与避免短暂矛盾导致频繁切换的设计。当前使用离线逐小节评分和 Viterbi，不是其 Dart HMM 实现移植；未包含 Dart 库。 | 0BSD（whatchord 仓库根许可证）；[原文](licenses/whatkey.txt)；[核对提交](https://github.com/EarthmanMuons/whatchord/tree/efd4c31ce7d98aeeb4233b43d49d835e6ead06fa) |
| [Michael Scott Asato Cuthbert / music21](https://github.com/cuthbertLab/music21) | ChordTheory.swift 使用相同的 Krumhansl–Kessler 大小调轮廓数值与相关方法；tools/CompareKeyAnalysis.py 可选调用其公开 API 做离线全曲对照。未分发 Python 库，使用者另行安装。 | BSD-3-Clause；[原文](licenses/music21.txt)；[核对提交](https://github.com/cuthbertLab/music21/tree/4e6d4d617cb0bb0259fe3c295276dd85dc07237e) |
| [Tone.js](https://github.com/Tonejs/Tone.js) | 调研其时钟 Ticker 的 Worker／timeout 方式。未导入、复制或打包框架，也未据此实现 Worker 调度。 | [上游 MIT](https://github.com/Tonejs/Tone.js/blob/dev/LICENSE.md) |
| [Kitenite/sync_metronome](https://github.com/Kitenite/sync_metronome) | 仅调研 README 描述的中心时钟校准与多人同步结构。没有引入代码或依赖，没有独立验证其声学误差。 | README 声明 MIT；核对时 GitHub license 接口未返回独立许可证，故不作为已验证可复用代码来源。 |

## 乐理来源

Krumhansl–Kessler 调性轮廓对应认知乐理中的 pitch-class profile 方法。公开实现依据见 [music21 discrete analysis](https://github.com/cuthbertLab/music21/blob/4e6d4d617cb0bb0259fe3c295276dd85dc07237e/music21/analysis/discrete.py) 和 [music21 官方分析文档](https://www.music21.org/music21docs/moduleReference/moduleAnalysisDiscrete.html)。ChordCue 还加入和弦音阶覆盖、时值、主和弦、属和弦与段落持续性的启发式评分。这些结果未经公开曲库准确率评估。

## 分发与后续引用

构建脚本把本文件、项目 MIT 及 licenses/ 里的参考许可随应用资源一同分发。当前参考关系依据源码注释与开发记录整理；后续 PR 如果实际复制或修改第三方代码，必须在代码邻近标注来源、精确版本、改动关系并保留完整许可。不要将“参考思路”误写成第三方代码已集成。

图标 Resources/ChordCue.svg 和 DemoChords.txt 为本项目提供的原创资源，随项目 MIT 分发。仓库不包含商业歌曲和弦快照或用户工程数据。

## Windows 运行依赖与离线通知

Windows 版运行依赖为 CPython 3.13 x64、PySide6 / Shiboken6 6.11.2 与 Qt 6.11.2；局域网服务使用 Python 标准库 asyncio。cx_Freeze 8.7.1 / freeze-core 的应用启动部分和应用本地 VC 运行库随冻结包分发。实际包版本与许可路径由安装资源中的 `licenses/installed-distributions.json` 记录。

Qt / PySide 的许可独立于 ChordCue 的 MIT。仓库保存 Qt LGPL v3、GPL v3 以及 GNU LGPL v2.1、LGPL v2、GPL v2 的完整文本。`licenses/Qt-6.11.2-THIRD-PARTY-NOTICES.txt` 汇编官方 Qt 第三方页面和 Qt WebEngine / Chromium 归属说明，保留其中的版权和许可正文；配套 manifest 记录来源与 SHA-256。汇编涵盖全部 Qt 模块和平台，是文档超集，不声称每一项均进入 Windows 载荷。

`licenses/PySide6-6.11.2-NOTICES.txt` 另外收录同版本官方源码归档的许可文件、归属记录和记录引用的全文，包括 Shiboken 使用的 Python 3.7 代码通知。`licenses/Qt-PySide-SOURCE-INFORMATION.txt` 将对应源码和动态库替换说明带入安装包，供离线查看。

最终程序资源会包含这些材料和所分发 Python 包附带的许可。VC 运行库条款另位于 `share/licenses/vc_redist`。完整源码入口、动态库替换／重建说明及尚需发行者确认的事项见 [Windows 第三方说明](windows/docs/THIRD_PARTY.md)。保留通知和上游链接不等于已经完成最终二进制及对应源码的发行审查。
