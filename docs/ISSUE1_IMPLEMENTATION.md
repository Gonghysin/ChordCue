# Issue 1：源谱导入与设备视图

本轮对应 [Gonghysin/ChordCue#1](https://github.com/Gonghysin/ChordCue/issues/1)。
功能分支为 `codex/issue-1-score-devices`，应用候选版本为 `0.3.5`，通过 Draft PR 进行源码审查。
候选安装包和开发主机验收记录保留在本地；既有 `0.2.0` / `0.3.0` / `0.3.1` / `0.3.2` / `0.3.3` / `0.3.4` 候选安装包与验收记录保持原样。
本说明记录实现范围，不代表所有平台和发布门槛已经验收。

## 功能与格式

`0.3.5` 移除手动添加 / 编辑 BPM 与拍号变化点的入口和弹窗，
改为只读显示当前速度与拍号。导入 `.gp`、`.gpx`、GP3/4/5、MusicXML 时，
从源文件读取 tempo / meter map，独立播放自动切换，主机、局域网节拍器、
跟随、定位、循环与 PDF 使用同一源时间线。BPM 以四分音符计。
源文件的弱起、中途变速、音符、技法和和弦位置保留。
旧 v1 / v2 工程和 `0.3.4` 已保存的 v3 变化表仍可读取、播放及保存。
本轮没有添加乐器模拟音源。

| 输入 | 已实现范围 | 验证边界 |
| --- | --- | --- |
| MusicXML `.musicxml` / `.xml` | partwise / timewise；声部、音符与休止、声部时值、调弦 / 弦品、显式和弦、调号、速度、拍号、分段、重复 / 结尾 | 原创多声部样例、分数时值、弱起和异常输入；不等于全部导出器兼容 |
| 压缩 MusicXML `.mxl` | 本地 ZIP 解包与容器根文件；同一解析器 | CRC、路径、展开体积及缺失根文件检查 |
| GP7/8 `.gp` | 使用打包的 alphaTab 导入为共同 ScoreIR，保留结构化音符与拍技法 | 原创 GP8 基础 / 技法样例与结构回归；不支持的效果显示来源警告 |
| GP6 `.gpx` | BCFS 和 BCFZ，展开前后限制与目录扇区检查 | 原创基础样例，压缩声明、截断、扇区回环和长度异常 |
| GP3/4/5 | 基础音符、调弦、弦品、显式和弦、速度、拍号和重复 | 原创可复现基础样例；旧商业乐谱库未进行全面验证 |

导入预览显示声部、警告和编译后的路线信息，取消 / 失败不会替换工程。
ScoreIR 保留全部声部和精确四分音符分数；保存当前声部与保存原谱是独立操作。
无源和弦时显示“无和弦标记”，不从音符推断和弦；未知调性也明确标注。

五线谱和 TAB 在本地 SVG 渲染。五线谱支持显示移调；TAB 需要原弦品资料，保持原指法。
和弦 / 级数显示与 PDF 使用源小节号、实际长度、拍号、精确和弦起点和源调性变化。
当前 PDF 导出为和弦 / 级数谱，不导出完整五线谱或 TAB。

独立 transport 按速度图积分，区分源小节与展开路线中的重复次数；支持开始 / 暂停 / 停止、
定位、整小节循环和约 400 ms 首拍准备。共享节拍器使用同一路线处理变拍号、弱起与重复。
Windows 源谱顶部 BPM / 拍号控件由源文件控制；Mac 提供会话内倍速，重新打开工程恢复源速度。
不生成乐器音频。

主机设备表可编辑名称，逐台分配声部及五线谱 / TAB / 和弦 / 级数 / 节拍器视图。
客户端以独立 ID 和恢复凭据注册，分配推送有版本 ACK；同一投放会话重连恢复分配。
网络 RTT、时钟校准状态、测时抖动、校准距今、音频输出延迟估计、最后更新和同步健康分别显示。
主机 / 浏览器单调时钟的起点映射只在诊断提示中说明，不把它当成延迟显示。
节拍器声音需客户端主动开启；页面隐藏、样本过期或失联会停止排程。

## 0.3.1 五项改进

用户已撤销乐器模拟音源功能，本轮保留既有节拍器，新增范围如下：

1. 时钟校准使用四时间戳有效探测，拒绝负 RTT、超时和乱序响应。探测十秒过期；
   重连、主机换会话、浏览器休眠 / 时间基准跳变后重新校准。正常界面显示校准质量，
   不再显示类似 `313276352 ms` 的起点差。新字段经能力协商发送，旧客户端可继续注册。
2. 主机声部、五线谱 / TAB 和播放跟随控件放在“乐谱”页面。无弦品资料的声部禁用 TAB，
   切换声部或打开工程时回到五线谱。TAB 保留源指法；五线谱可独立显示移调。
3. 播放跟随按实际渲染行判断换行，本机和 LAN 客户端把新行对齐可见区域顶部；
   浏览器留出固定页眉，末行有足够底部留白。关闭跟随可手动阅读，同时保留播放高光。
4. 按实际音符头 / TAB 品位的边界显示高光框；同时显示多声部持续音、和弦音和休止。
   暂停保留位置；失联、过期、隐藏页面及替换乐谱时清除旧框。布局变化后重新取边界。
5. ScoreIR v2 保存击弦、勾弦、滑音、推弦曲线、揉弦、泛音、掌根制音、死音、延音、
   拨弦方向及其他已支持的结构化技法。跨音符技法保留明确目标，H/P 以连线和文字呈现。
   原谱无泛音触弦节点时保留源音高 / 指法并提示；无法表达的源效果带位置警告。

工程外层版本仍为 1 / 2。旧 ScoreIR v1 严格校验后迁移为 v2；旧导入器已经丢弃的技法
需要重新导入源文件，不能从旧工程恢复。未来版本或无效技法会在覆盖目标文件前被拒绝。

## 0.3.2 连续校准与大谱投放

健康播放期间可继续校准时钟。RTT 不超过 60 ms 的可靠探测刷新校准新鲜度，低 RTT 样本提供起点映射估计；
映射最多以每秒 5 ms 平滑修正，不撤销已排程的拍点。本机浏览器桥接与 LAN 使用共同实现。
首次尚无可靠探测时保持校准中，首个可靠探测直接建立映射；拥堵探测不延长有效期。
60 ms 上限来自四时间戳估计最多 RTT/2 的误差界，匹配既有 30 ms 未来样本容差。
首次尚无校准、超过十秒没有可靠探测、休眠 / 时间跳变、换会话或失联仍会停止音频排程，
重新建立可靠映射后恢复。正常连续校准的测试不等于实体声卡同步验收。

局域网投放在连接 / 换谱时发送完整结构化乐谱和展开路线，各客户端在本地选择分配的声部与视图。
播放期间只推送位置、速度和状态，客户端据时钟映射进行本地跟随和既有节拍器排程。
源谱已包含的和弦 / 调号资料不再重复生成旧适配数组，减少同一帧的冗余。

原 Windows 2 MiB chart 上限改为共同预算：chart 总计 65 MiB，ScoreIR 和路线各 32 MiB，
其他元数据 1 MiB；transport 仍为 64 KiB。合法数据超过旧上限可以继续投放，
超出独立预算时明确报出对应字段及容量。传输按 64 KiB 连续分块，整帧完成后才发送分配和播放状态，
每次发送只保留最新待发状态；慢连接采用每块五秒的停滞检查。
不同正在发送的 chart 帧共享 256 MiB 预算，超额先关闭持有最旧帧的连接；
这不是整个应用的内存上限。整帧完成和停止投放时释放相应缓存。

## 0.3.3 音频延迟估计显示

主机原“声音补偿”列改为“音频输出延迟估计”，其数据来自客户端浏览器的音频属性，
与手动额外补偿独立。未创建 / 暂停音频上下文，或没有可用的正 `outputLatency` 时，
上报 null 并显示“未提供估计”；浏览器全部报告零不被视为物理零延迟。
只有 `baseLatency` 时，客户端显示已知处理部分，设备输出估计保持未知。
有效输出设备估计可与已知处理延迟相加，缺少处理属性时明确只含输出设备部分。
试听后运行中的音频上下文也可提供估计，不要求节拍器正在播放。

有效 `getOutputTimestamp()` 继续优先用于排程映射；其与音频当前时间的差值不再用作
可靠输出延迟指标。排程回退与最多 150 ms 补偿、25 ms 排程检查、140 ms 实际排程上限保持一致。
客户端文案分别显示输出映射来源、可用延迟范围、手动目标及实际渐进应用值。
网络 RTT 不作为音频输出延迟；耳机 / 蓝牙物理误差仍需实测。

## Subagent 分工

执行 subagent 均按用户指定使用 **GPT-6.1 Sol**。原 Issue 1 阶段使用 **xhigh**；
0.3.1 的时钟 / 协议修复使用 **high**，模型 / 导入 / 技法和最终独立复核使用 **xhigh**。
各阶段由原会话协调，没有创建替代开发会话。

| 任务 | 负责代理 | 交付 |
| --- | --- | --- |
| A1：共同模型与工程 | `a1_score_domain` | Python / Swift ScoreIR、校验、精确分数、v1/v2 工程和跨语言 oracle |
| A2：独立播放 | `a1_score_domain` | 重复 / 结尾与有限跳转路线、速度积分、定位 / 循环、共享浏览器路线 |
| 浏览器 / 设备 / 格式回归 | `browser_devices` | 视图分配、恢复与 ACK、真实三浏览器场景、旧 GP 样例和独立审查 |
| Mac 前端 | `mac_score_lan` | WebKit 导入 / 渲染、工程、独立模式、设备协议、原生节拍器和 PDF |
| 集成与 Windows | 原会话主代理 | 离线资源、导入 UI、源谱显示 / PDF、设备表、冻结程序、MSI 和最终验证 |
| 0.3.1：时钟 / 协议 | `sync_clock_fix`，high | 校准、遥测兼容性、时钟质量展示及独立复核 |
| 0.3.1：技法模型 / 导入 | `techniques_domain`，xhigh | ScoreIR v2、MusicXML / GP 技法、Python / Swift 校验和工程回归 |
| 0.3.1：显示与集成 | 原会话主代理 | 主机控件、实际行跟随、音符框、技法渲染、Mac 接口、真实 Qt / LAN 和本地候选包 |
| 0.3.2：连续校准 | `sync_clock_fix`，high | 平滑时钟修正、持续有效探测、本机 / LAN 连续节拍回归 |
| 0.3.2：容量与独立复核 | `techniques_domain`，xhigh | 大谱报错复现、Python / Mac 帧发送复核、Mac 共享帧预算与契约 |
| 0.3.2：投放与验收 | 原会话主代理 | Python 容量与分块、真实 Qt / TCP 大谱、慢连接 / 内存边界、冻结包和最终验证 |
| 0.3.3：音频诊断 | `sync_clock_fix`，high | 可用音频估计、未知 / 暂停状态、输出映射与手动补偿分离、JS 回归 |
| 0.3.3：文档与独立核对 | `techniques_domain`，xhigh | 输出 API 语义、共同契约和 Mac 验证边界 |
| 0.3.3：主机与集成验收 | 原会话主代理 | 设备表、实际 AudioContext / HTTP 遥测、冻结包和最终验证 |
| 0.3.4：时间线领域与播放 | `a1_score_domain`，GPT-6.1 Sol，xhigh | 变化表、逐小节容量、schema 3、源谱无损改写、循环边界 |
| 0.3.4：Mac 兼容实现 | `mac_score_lan`，GPT-6.1 Sol，xhigh | Swift 编辑 / 保存 / route、LAN 小节元数据、PDF 和静态核对 |
| 0.3.4：客户端与节拍器 | `sync_clock_fix`，GPT-6.1 Sol，high | 有效拍号和速度显示、预测路线、循环浮点边界、真实脚本排程回归 |
| 0.3.4：独立审核 | `techniques_domain`，GPT-6.1 Sol，xhigh | Swift 接口、跨平台时间语义与编辑边界 |
| 0.3.4：Windows 集成 | 原会话主代理 | 变化表界面、文本 / PDF / LAN、真实浏览器、冻结程序及 MSI 验证 |
| 0.3.5：源谱自动时序验证 | `a1_score_domain`，GPT-6.1 Sol，xhigh | 实际文件导入、tempo / meter 自动切换、弱起 / 中途变速及原有工程兼容 |
| 0.3.5：Mac 界面 | `mac_score_lan`，GPT-6.1 Sol，xhigh | 移除手动变化表，保留只读显示与源谱自动路线 |
| 0.3.5：Windows 集成 | 原会话主代理 | 移除变化表、只读状态、真实导入与冻结程序测试、最终 MSI |

## 验证入口

Windows 在锁定依赖环境下运行：

```powershell
$env:PYTHONPATH = 'windows/src'
windows/.venv/Scripts/python -m ruff check windows/src windows/tests windows/packaging windows/scripts tools/vendor_score.py
windows/.venv/Scripts/python -m mypy --config-file windows/pyproject.toml windows/src/chordcue
windows/.venv/Scripts/python -m pytest -q windows/tests
node --test windows/tests/js/*.test.cjs
windows/scripts/build.ps1 -SkipMsi
windows/.venv/Scripts/python windows/packaging/setup.py bdist_msi --skip-build
windows/.venv/Scripts/python windows/packaging/verify_artifacts.py --msi windows/dist/ChordCue-0.3.5-win-x64.msi --report windows/dist/0.3.5-msi-tables.json
$env:CHORDCUE_MSI = (Resolve-Path windows/dist/ChordCue-0.3.5-win-x64.msi).Path
$env:CHORDCUE_FROZEN_DIR = (Resolve-Path windows/build/exe).Path
windows/.venv/Scripts/python -m pytest -q windows/tests/test_packaging_artifacts.py
windows/scripts/smoke.ps1 -OutputDirectory windows/smoke-results/musicxml -ScoreFile windows/tests/fixtures/issue1-original.musicxml
windows/scripts/smoke.ps1 -OutputDirectory windows/smoke-results/guitarpro -ScoreFile windows/tests/fixtures/issue1-original.gp
```

受限环境不能创建 Node 测试子进程时，可用 `--test-isolation=none` 执行相同测试。
MSI 数据库 / 完整冻结载荷检查，以及真实 Qt 的 staff / TAB、PDF 和三浏览器分配 / 重连，
均已在开发主机执行。本地 `outputs/` 保存结果 XML、Node 日志、构建日志和冻结版截图，
不作为仓库源文件提交。修改后须重新构建才可把源代码测试结果归于具体安装包。
0.3.1 的最终完整运行通过 457 项 Python / Qt / 包装检查，跳过 17 项 Swift 依赖检查；
83 项共享 JavaScript 回归、Ruff / mypy、三组冻结版冒烟均通过。
本地最终记录见 `outputs/score-improvements/验收记录.md`；不同版本 MSI 在 dist 中保留时，
须显式选择当前版本进行审计，避免把旧包视为新源码产物。

0.3.2 的最终完整运行通过 464 项 Python / Qt / LAN / 包装检查，跳过 17 项 Swift 依赖检查；
91 项共享 JavaScript、Ruff / mypy、三组冻结版冒烟和实际 MSI 审计通过。
93 项目标 LAN / Qt / 设备回归包含超过旧 2 MiB 上限的大谱、慢连接、连续分块、
独立容量和旧帧预算；本机桥接与 LAN 的后台校准各完成 100 次循环、401 个模拟音频拍点，
变速变拍路线完成 100 次循环、701 个拍点。结果不代替不同实体设备的听音 / 声卡同步验收。
最终本地记录见 `outputs/clock-lan-fixes/验收记录.md`，旧版本记录不覆盖。

0.3.3 的完整 Python / Qt / LAN / 包装验收分两阶段执行：源码与冻结载荷检查期间暂缓终包策略，
MSI 完成后单独运行该检查；按测试标识合并为 465 项通过、17 项 Swift 检查跳过。
103 项共享 JavaScript、Ruff / mypy、三组冻结版冒烟和实际 MSI 审计通过。
真实 Qt / AudioContext 的受控属性经 HTTP 遥测传至主机，验证零报告保持未知、
8 ms 处理加 24 ms 输出显示 32 ms、暂停恢复未知，并验证实际设备表文案。
最终本地记录见 `outputs/audio-estimate-fix/验收记录.md`；测试属性不代表用户实体设备的声学实测。

0.3.4 的最终源码完整运行通过 530 项 Python / Qt / LAN / 冻结载荷检查，暂缓终包策略；
最终 MSI 完成后该单项通过，按测试标识替换为 531 项通过、17 项 Swift 环境检查跳过。
112 项共享 JavaScript、Ruff / mypy 22 个源码文件、三组冻结版冒烟和实际 MSI 策略检查通过。
冻结手工谱验证变化表、v3 保存重开、按实际 BPM / 拍号定位及 PDF；
GP / MusicXML 冻结谱验证修改起始速度后音符不变，五线谱和 TAB 正常。
真实浏览器覆盖晚加入、定位、新 revision 的变化路线与拍号速度显示；
混合 4/4→6/8→7/8、120→90→150 BPM 的模拟音频排程连续 100 轮、1701 拍无遗漏或重复。
主机循环端点与前后 1 ns 在 1000 轮仍一致。结果不代替实体设备声学同步。
最终本地记录见 `outputs/timing-presets/验收记录.md`，旧候选包与报告保持原样。

0.3.5 移除手动变化编辑入口，保留源谱自动时序和旧 v3 工程读取。
原配 Python 环境的完整重跑通过 545 项、暂缓最终 MSI 策略；该单项通过后，
按测试标识合并为 546 项通过、17 项 Swift 环境检查跳过。
112 项共享 JavaScript、Ruff / mypy 21 个源码文件、三组冻结版冒烟和实际 MSI 策略检查通过。
GP8 与 MusicXML 原文件不经手动修改，播放自动从 4/4、♩=120 切换为 6/8、♩=135。
17 项真实导入时序回归覆盖 GP3 / 4 / 5、GP6 BCFS / BCFZ、GP8、MusicXML，
以及弱起、中途变速、反复段速度恢复与速度单位换算。
GP7 的本轮时序验证使用 GP8 容器改版号的兼容性变体，不等于独立 GP7 导出验证。
首轮另一同版本 Python 路径运行曾产生 Qt 事件循环底层异常，所有断言仍通过；
原配环境对照和完整重跑均无此异常。原始日志保留，未关闭 faulthandler，
尚未定位具体故障 DLL。最终记录见 `outputs/automatic-timing/验收记录.md`。

PR 的 CI 检查另修复了三处问题：Mac 导入 / 保存路径的 `catch` 将错误写入
`self.error`，避免遮蔽被捕获的错误；LAN 行跟随用例改用最后一小节循环和实际分配确认，
避免在慢 runner 上错过仅两秒的播放窗口；初次拥堵时钟探测不再直接建立“有效”映射。
256 ms 非对称探测后接 6 ms 探测的回归覆盖了新样本被误判为未来数据的情况。
既有 `[-30,350]` ms 样本窗口保留，后台校准仍最多每秒调整 5 ms。
新增本机桥接 / LAN 各 100 次循环校准回归，没有重复、漏拍、取消或迟到；
共享 JavaScript 当前共 117 项通过。CI 修复记录和候选包另存本地 `outputs/ci-clockfix/`，
保留原 `0.3.5` 候选证据。
修复后本地完整回归通过 545 项；最终 MSI 策略单项通过后，按测试标识合并为
546 项通过、17 项 Swift 环境检查跳过。新冻结程序的三组冒烟、载荷与 MSI 表检查通过，
Ruff / mypy 21 个源码文件和日志中的底层异常扫描均通过。

Mac 检查命令与原生人工流程见 [Mac 验证说明](MAC_ISSUE1_VERIFICATION.md)。
2026-10-02 的 [macOS CI](https://github.com/JoeyZhuoer/ChordCue/actions/runs/37023991926)
已通过原生 arm64 编译、临时签名、位置检查、乐谱 / 时间线 oracle、实际 Swift 差分检查和应用归档。
原生界面、Logic Pro、PDF 视觉与实体音频仍需实机验收。

## 限制与待验证项

- 导入文件及展开后的 XML / JSON 有 32 MiB 上限；ScoreIR 和路线还有事件、分数精度与展开次数上限。
  LAN 分别限制 ScoreIR、路线、元数据和总帧大小，超限会明确拒绝投放。具体契约见 [共同接口](ISSUE1_CONTRACT.md)。
- 小节中途调号保留在源模型和和弦 / 级数计算中；五线谱当前小节按起始调号排版，下小节继承新调号。
  后续谱号变化和未覆盖的演奏技法尚未完整呈现，会显示警告。
- DC / DS / Coda / Fine 支持已解析的小节边界跳转与小节起始目标；中途或歧义跳转带警告并安全延续。
  不能把所有复杂记谱结构都视为已支持。
- 真实三浏览器测试运行于同一开发主机。不同实体设备、浏览器后台政策、声卡 / 耳机输出和声学误差仍需实测。
- Mac 原生交互验收和 Intel 构建、干净 Windows 10 / 11 的 MSI 生命周期与长时间实体音频仍待完成；
  历史发布门槛见 [Windows 验证记录](../windows/docs/VERIFICATION.md)。
