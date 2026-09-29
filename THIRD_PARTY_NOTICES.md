# 第三方来源、参考与许可证

ChordCue 自身采用 MIT。下表区分运行依赖、算法／结构参考与仅调研项目；参考项目的作者及许可不因本项目 MIT 而被替代。

核对日期：2026-09-29。仓库未打包下列第三方运行库的源码或二进制。系统框架由 macOS 提供。

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
