# 贡献指南

欢迎提交 Issue 和 Pull Request，尤其是 Windows、其他宿主适配及 iReal Pro 导入方向。先阅读 [TODO](TODO.md) 与 [使用手册](docs/USER_GUIDE.md)。

## 本地开发

```bash
git clone https://github.com/Gonghysin/ChordCue.git
cd ChordCue
git switch -c feature/your-change
bash scripts/build.sh
```

- `Sources/LogicReader.swift`：macOS 辅助功能读取，负责宿主位置、BPM、拍号与和弦。
- `Sources/ChordTheory.swift`：符号、移调、级数、定调与转调。
- `Sources/ChordCue.swift`、`Sources/ChartPDF.swift`：应用 UI 与 PDF。
- `Sources/LANBroadcast.swift`：本地 HTTP、SSE 和时钟接口。
- `Sources/NativeMetronome.swift`：本机 WKWebView 桥。
- `Resources/Broadcast.html`、`Resources/Metronome.js`：浏览器谱面与本地音频排程。

Swift UI 保持中文；文档和 Issue 可用中文或英文。尽量提交聚焦的小改动。跨平台或新宿主实现前，建议先用 Issue 说明数据来源、时钟接口与依赖许可证。

## 检查与 PR 内容

项目暂没有自动测试套件，不要宣称未经实际测量的准确率或同步精度。

可做语法及构建检查：

```bash
bash -n scripts/build.sh scripts/install.sh
node --check Resources/Metronome.js
bash scripts/build.sh
```

PR 请说明改动目的、受影响的平台／宿主、验证步骤和结果、已知限制。涉及同步时描述开始、停止、拖动、循环、速度／拍号变化、断流及页面切换行为；涉及音频时注明输出设备和浏览器。新依赖或参考代码需注明来源、版本和许可证，并更新 THIRD_PARTY_NOTICES 与相应许可文本。

## 数据与隐私

不要提交私人 Logic 工程、完整歌曲快照、录音、未授权的和弦谱、账户凭证、证书或私钥。错误报告优先用最小原创和弦序列，脱敏工程名和绝对路径。

应用读取宿主，不应未经明确用户操作修改工程或开启网络端口。当前没有遥测；增加外部网络请求应先讨论用途与控制方式。

## 可选调性对照工具

`tools/KeyAnalysisSnapshot.swift` 读取辅助功能文本快照；`tools/CompareKeyAnalysis.py` 使用 music21 进行离线算法对照。应用本身不依赖 Python。

```bash
xcrun swiftc -parse-as-library Sources/LogicReader.swift Sources/ChordTheory.swift tools/KeyAnalysisSnapshot.swift -o /tmp/chordcue-key-analysis
uv run --with music21 python tools/CompareKeyAnalysis.py /tmp/chordcue-key-analysis path/to/your-snapshot.txt 4
```

相关系数不是准确率；不要把全曲算法结果当作转调边界的正确性证明。

## 协议

提交贡献表示你有权按本项目 MIT 协议提供相关代码；第三方内容继续保留其原始许可。
