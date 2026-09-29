# 截图来源

这些截图来自 ChordCue 的真实 macOS 界面，使用 `tools/ScreenshotLogicReader.swift` 提供的原创演示和弦、4/4 拍和 120 BPM。播放位置固定在第 5 小节第 3 拍，不连接 Logic，不含私人歌曲或工程信息，也不用于证明同步精度。

截图保留原始捕获内容，没有合成界面或修改文字。截图入口替代 `Sources/LogicReader.swift` 编译，只用于制作文档；正式构建脚本不会包含它。

从仓库根目录先执行 `bash scripts/build.sh`，再在独立的演示应用包中使用以下编译命令：

```bash
xcrun swiftc -target arm64-apple-macos13.0 -parse-as-library \
  Sources/ChordCue.swift tools/ScreenshotLogicReader.swift \
  Sources/ChordTheory.swift Sources/ChartPDF.swift \
  Sources/LANBroadcast.swift Sources/NativeMetronome.swift \
  -o build/ChordCueDemo.app/Contents/MacOS/ChordCueDemo
```

演示包须使用独立的 bundle identifier，例如 `local.codex.chordcue.screenshot-demo`，并配置对应的 `CFBundleExecutable`、资源和签名，以避免与正式应用的设置混用。
