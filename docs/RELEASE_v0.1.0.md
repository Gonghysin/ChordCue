# ChordCue v0.1.0

首个可下载的 macOS 版本，与公开源码对应。

## 下载与安装

- 应用：`ChordCue-v0.1.0-macos-arm64.zip`。
- 校验文件：`SHA256SUMS.txt`。
- 平台：Apple Silicon（M 系列芯片），macOS 13 或更新版本。
- 宿主：提供和弦轨的 Logic Pro；当前读取逻辑依赖英语辅助功能字段。

解压 ZIP，将 `ChordCue.app` 放到“应用程序”后启动。若已有同名应用，请先退出再替换。运行不需要 Xcode 或 Homebrew。

**此包使用 ad-hoc 临时签名，尚未经过 Apple Developer ID 签名或公证。** 首次运行可能被 macOS 拦截；确认来源并核对校验值后，参照 [Apple 官方说明](https://support.apple.com/zh-cn/102445) 在“系统设置 → 隐私与安全性”中选择“仍要打开”。随后开启 ChordCue 的辅助功能权限，打开 Logic 工程并点“刷新 Logic 和弦”。

切换签名身份、替换旧应用或下载后续版本，可能需要重新授权。完整步骤见 [安装说明](https://github.com/Gonghysin/ChordCue/blob/v0.1.0/docs/INSTALL.md)。

校验方式（在下载目录执行）：

```bash
shasum -a 256 -c SHA256SUMS.txt
```

## 本版功能

- 跟随 Logic 播放头显示和弦网格、大字当前和弦，以及工程名、拍号和 BPM。
- 和弦／级数切换、显示移调、自动定调与候选转调、手动调性覆盖。
- 和弦谱及级数谱 PDF 导出。
- 局域网浏览器跟谱，每人独立选择显示调与级数。
- 经典节拍器／鼓机、三格重音柱，八分音符模式下 4/4 显示 8 柱。
- 看和弦谱时节拍器可同时工作；顶部可独立开启或关闭。
- 可调整窗口大小，并切换窗口置顶。

## 已知限制

- 当前仅支持 macOS + Logic Pro；Windows、其他宿主和 iReal Pro HTML 导入尚未实现。
- 节拍器暂支持四分音符为拍单位的 1–12 拍；完整变拍号／未来速度地图尚未接入。
- 定调依据已有和弦，不能保证关系大小调或转调边界的判断正确。
- 依赖宿主 UI 更新、网络与输出设备延迟，不承诺采样级同步或专业耳返精度。
- 局域网共享为 HTTP，访问链接持有者可读谱；不建议映射到公网。
- 此包已在 Apple Silicon 构建并校验签名；未在全新下载环境验证 Gatekeeper／授权流程，也未实机验证 Intel Mac。

包内只有原创演示和弦，不包含私人歌曲快照或用户工程。MIT 协议及参考项目许可证随应用保留。欢迎通过 [TODO](https://github.com/Gonghysin/ChordCue/blob/v0.1.0/TODO.md) 和 [贡献指南](https://github.com/Gonghysin/ChordCue/blob/v0.1.0/CONTRIBUTING.md) 提交 PR。
