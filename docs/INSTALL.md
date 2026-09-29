# 安装、更新与故障排查

## 下载版安装

1. 打开 [v0.1.0 Release](https://github.com/Gonghysin/ChordCue/releases/tag/v0.1.0)，下载 `ChordCue-v0.1.0-macos-arm64.zip` 和 `SHA256SUMS.txt`。此包仅支持 Apple Silicon（M 系列芯片）及 macOS 13+，不支持 Intel Mac 或 Windows。
2. 可在下载目录执行 `shasum -a 256 -c SHA256SUMS.txt` 核对文件完整性。校验值用于比对发布文件，不代表 Apple 公证。
3. 退出正在运行的 ChordCue，解压 ZIP，将 `ChordCue.app` 放到“应用程序”。若已有同名应用，替换会覆盖旧安装。
4. 双击启动。如果系统提示无法验证开发者，确认文件来自本仓库且校验一致后，按 [Apple 官方说明](https://support.apple.com/zh-cn/102445) 在“系统设置 → 隐私与安全性”选择“仍要打开”，再确认“打开”。不要全局关闭 Gatekeeper。
5. 点击“辅助功能授权”，允许已安装的 ChordCue，随后打开 Logic 工程并点“刷新 Logic 和弦”。

该下载包使用 ad-hoc 临时签名，未经过 Apple Developer ID 签名或公证。如果使用过本地固定签名版，切换到此包后可能需要重新授权；后续下载更新也可能再次要求授权。固定签名开发版的更新方法见下文。

运行下载版不需要 Xcode、Node.js、Python 或 Homebrew。包内包含原创演示和弦以及 MIT／第三方许可，不包含私人工程或商业歌曲快照。

## 构建环境

- macOS 13 或更新版本。
- Xcode Command Line Tools：`xcode-select --install`。
- Logic Pro 必须提供全局和弦轨。当前未确定所有兼容版本，版本变化可能改变辅助功能字段。
- Apple Silicon 已本地构建；Intel 可用 `CHORDCUE_ARCH=x86_64 bash scripts/build.sh` 构建，但尚未实机验证。
- 可选 `brew install librsvg`，构建脚本将 SVG 转为多尺寸应用图标。应用运行不需要 Homebrew。
- Node.js 仅用于开发时 JS 语法检查；Python/music21 仅用于可选分析对照。常规构建无需这些工具。

## 编译与安装

```bash
git clone https://github.com/Gonghysin/ChordCue.git
cd ChordCue
bash scripts/build.sh
```

输出为 `build/ChordCue.app`。可先运行 `open build/ChordCue.app`。

安装前退出当前应用，再执行：

```bash
bash scripts/install.sh
open /Applications/ChordCue.app
```

此操作覆盖同名安装。个人目录安装可使用：

```bash
CHORDCUE_INSTALL_DIR="$HOME/Applications" bash scripts/install.sh
open "$HOME/Applications/ChordCue.app"
```

不把签名证书或私钥放进仓库。脚本构建目标与当前机器架构一致，最低系统版本为 macOS 13。

## 辅助功能授权

点击应用里的“辅助功能授权”，进入系统设置 → 隐私与安全性 → 辅助功能，允许实际安装位置的 ChordCue。系统可能要求密码或 Touch ID，必须由用户完成。

读取不到时：

1. 确认 Logic 正在运行，工程打开，Chord 全局轨可见。
2. 切换 Logic 英语界面，显示带小节、拍、division、tick 的完整位置；按拍显示可降级跟谱，但无法提供精确节拍器同步。
3. 点击“刷新 Logic 和弦”。
4. 若改了签名身份或移动应用，关闭再开启权限，并重新启动应用。

仅在确实需要重新授权时，可清除本应用权限：

```bash
tccutil reset Accessibility local.codex.chordcue
```

先退出应用，重置后在系统设置手动授权。该命令不能自动授予权限。

## 固定签名与后续更新

默认构建使用 ad-hoc 临时签名，每次更新可能再次需要授权。更稳定的方式是始终使用同一证书、bundle identifier 与安装位置。

你可以使用已有的 Apple Developer 代码签名身份；本地开发也可以在“钥匙串访问”的“证书助理”中创建自签名的代码签名证书，完成系统需要的信任设置。各 macOS 版本界面可能不同。

查看有效签名身份：
```bash
security find-identity -v -p codesigning
```

把自己的身份名称或其 SHA-1 标识传给构建脚本：
```bash
CHORDCUE_SIGNING_IDENTITY="你的有效代码签名身份" bash scripts/build.sh
CHORDCUE_SIGNING_IDENTITY="你的有效代码签名身份" bash scripts/install.sh
```

更新源码：
```bash
git pull --ff-only
CHORDCUE_SIGNING_IDENTITY="你的有效代码签名身份" bash scripts/install.sh
```

证书名称一致不等于证书身份一致；保留原证书及私钥。证书过期、重新生成、换机器或系统权限策略变化仍可能需要重新授权。本地自签名也不等于 Apple 公证。

## 常见问题

### 没有和弦或不同步

ChordCue 读取 Logic UI，需辅助功能授权与可见的和弦轨。未连接时只是演示谱，不代表已经同步。MIDI 控制栏的 No Out 与本应用无关。

### 声音打不开或不响

先用“试听一拍”确认系统输出及个人音量；再开启节拍器并在 Logic 播放。查看节拍器状态是否在等待精细位置、校时或数据恢复。启动等待超过 3 秒会报错，可以重试。手机浏览器进入后台／锁屏会停声，需要返回后重新开启。

### 投放链接打不开

两台设备需在同一局域网。允许 macOS 防火墙传入连接，避免访客 Wi-Fi 隔离；换网络后停止并重新开启投放。只看到 127.0.0.1 时尚未找到可共享的 IPv4 地址。不要把端口映射到公网。

### 应用图标没有显示

安装可选 librsvg 后重新构建；没有此工具时构建脚本会跳过图标生成。

### 系统阻止运行下载版

Release 下载包尚未经过 Apple 公证。按上面的“下载版安装”核对来源与校验，再参考 Apple 官方提示操作。如果提示应用损坏，先重新下载并核验校验值；不要直接假定是权限问题。
