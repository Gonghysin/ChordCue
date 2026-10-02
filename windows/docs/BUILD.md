# Windows 开发、构建与排查

## 依赖和运行

应用使用 CPython 3.13 x64、PySide6 6.11.2 / Qt 6.11.2 和 Python 标准库。cx_Freeze 8.7.1 负责冻结目录与 MSI；测试和检查工具属于开发依赖。精确版本、下载文件哈希以 `windows/requirements.lock` 为准。

LAN 采用标准库 `asyncio` 实现有限的 HTTP / SSE 协议，未引入 aiohttp。这个选择用于在解析请求头之前统一限制连接数量，并实施 40 个连接、8 KiB 请求头和 5 秒头部读取期限等边界。它只服务固定路由，不提供通用 Web 应用执行能力。具体路由和流上限以 `src/chordcue/lan.py` 及协议测试为准。

在仓库根目录执行：

```powershell
py -3.13 -m venv windows/.venv
windows/.venv/Scripts/python -m pip install --require-hashes -r windows/requirements.lock
windows/.venv/Scripts/python -m pip install --no-deps --no-build-isolation -e windows
windows/.venv/Scripts/python -m chordcue.main
```

Node.js 用于运行共享浏览器调度器测试，不是安装后的应用依赖。开发验证环境使用 Node.js 24。原 macOS Swift 构建保持独立；Windows 源码在 `windows/src/chordcue`，共享浏览器资源仍是根目录的 `Resources/Broadcast.html` 和 `Resources/Metronome.js`。

## 测试与安装包

```powershell
windows/.venv/Scripts/python -m pytest windows/tests -q
windows/.venv/Scripts/python -m ruff check windows/src windows/tests
windows/.venv/Scripts/python -m mypy windows/src/chordcue
windows/scripts/build.ps1
windows/scripts/smoke.ps1
```

`build.ps1` 输出 `windows/build/exe` 与 `windows/dist/*.msi`，并检查最终文件和 MSI 表。`smoke.ps1` 对冻结的完整程序运行诊断，保存 JSON、截图和 PDF 等证据。音频诊断使用 OfflineAudioContext；不发出实体扬声器声音，不测量声学误差。

Qt 依赖探针可以先单独构建：

```powershell
windows/scripts/build.ps1 -Prototype
windows/scripts/smoke.ps1 -Prototype -OutputDirectory windows/smoke-prototype
```

探针成功仅说明测试主机上的冻结 Qt、WebEngine、离线音频和 PDF 依赖可加载。完整应用、MSI 生命周期和真实设备测试仍须分别执行。安装包结构、升级代码、事务回滚顺序和可选 MSI 生命周期测试详见 [packaging/README.md](../packaging/README.md)。未经生命周期测试的策略不能标为已验证。

原 macOS 构建和 Swift 乐理对照测试需要 macOS / Swift 工具链。Windows 运行时的跳过结果不能替代该平台上的通过结果。

## 离线第三方通知

仓库内的 Qt 许可汇编由下列脚本生成，不在每次应用启动时联网：

```powershell
windows/.venv/Scripts/python windows/packaging/collect_qt_notices.py
windows/.venv/Scripts/python windows/packaging/collect_qt_notices.py --offline
```

原始网页和 PySide 对应版本源码 ZIP 缓存默认在仓库同级的 `../qt-license-cache`，不提交。脚本检查 Qt 版本、提取完整归属说明与许可正文，并记录原始 HTML、源码归档和提取文本 SHA-256。同一缓存生成同一输出；`--offline` 要求缓存完整。`--refresh` 重新取得官方页面；Qt 文档版本已经变化时会拒绝继续，需要按新依赖重新审核。

`packaging/collect_licenses.py` 另外收集构建环境中实际分发包的许可和版本清单，以及 Python 和项目根目录 `licenses/` 的全部文件。最终包还需对照包含的 DLL、插件、WebEngine 资源、VC 运行库及源码获取信息进行审查，不能以“脚本没有报错”代表所有发行义务已满足。

## 常见问题

- **节拍器页空白**：确认安装或冻结目录完整，尤其是 QtWebEngineProcess、Qt 插件、资源与 locales；不要仅复制 `ChordCue.exe`。保留 smoke 的 stderr 和 JSON。开发运行应从匹配的锁定环境启动。
- **没有声音**：先看是否主动启用本机声音、正在播放、音量和拍点强度是否非零，再检查系统输出设备。锁屏／挂起会关闭声音；蓝牙会引入额外且可能变化的延迟。
- **局域网打不开**：确认投放已经启用、使用本次显示的链接、双方处在可互访网络、防火墙允许对应可信网络。停止投放后的旧链接应失效。
- **文本或项目被拒绝**：查看行号和范围，确认没有重复起点、曲长足够且项目版本受支持。遇到未来版本项目请保留原文件，并使用新文件名另存为。
- **无法安装或更新**：使用对应的 x64 Windows 目标系统；保留 `msiexec /i <安装包路径> /L*v <日志路径>` 的日志。修复和卸载应通过 Windows Installer，不要直接删除 Program Files。
- **中文字符或路径问题**：记录具体字体、Windows build、路径是否含空格或中文，并使用不含私人内容的最小项目复现。干净系统上的这些情况列为发布验收项。
