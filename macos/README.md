# Next：桌面和 Dock 启动

当前应用位置：`/Applications/Next.app`（已移到“应用程序”文件夹）。

文中的终端命令均在项目根目录（包含主 `README.md` 的 `next` 文件夹）运行。如果终端位于它的上一级目录，先执行 `cd ./next`；如果已在项目根目录，则无需切换目录。

## 平时怎么用

1. 双击“应用程序”里的 **Next**，或点击 Dock 图标。应用会检查本机端口；Next 未运行时，会用现有 `.venv` 在后台启动。
2. 等待页面就绪后，自动在**默认浏览器**打开。默认地址是 `http://127.0.0.1:8765`；如果你在现有配置里修改过 `port`，启动器会使用配置中的端口。
3. 重复双击只打开页面，不重复启动服务器。启动过程中也通过文件锁串行处理。
4. 要放进 Dock，将“应用程序”中的 **Next** 图标拖到 Dock 的应用区域，或者在运行中的 Next 图标上右键 → 选项 → 在程序坞中保留。

Next 应用保持打开时，每约 5 秒检查一次本地服务；只读取本地静态页面，不调用 Todoist 或 AI。如果它原先复用了 Terminal 启动的服务，而 Terminal 随后关闭，Next 会自动启动自己的后台服务并重新打开页面。正常服务不会被重复启动，也不会不断打开浏览器。恢复失败时只提示一次，解决问题后再次点击 Next 即可重试。

不会弹出 Terminal，不需要重新输入 API Key。现有任务逻辑、Todoist、AI 配置、Keychain 和页面不变。

## 怎么关闭

- 右键 Dock 中的 **Next** → **退出**，或激活 Next 后按 **Command-Q**。这会正常关闭本启动器自己启动的后台服务器。
- **只关闭浏览器标签页不会停止后台服务器。** 下次点 Next 会再次打开页面。
- 如果 Next 原本由 **Terminal** 手动启动，桌面应用会复用它，不会结束那个进程。Terminal 随后关闭时，只要 Next 应用仍开着，就会自动恢复自己的后台服务。要完全停止 Next，请在 Dock 中“退出”；如果手动启动的服务仍在，还需在原 Terminal 按 **Control-C**。
- 如果启动器意外被强制退出，后台服务可能仍在运行。重新打开 Next，再正常“退出”即可关闭由启动器管理的服务。

备用的关闭命令（只关闭启动器自己管理的服务，不会根据端口杀进程）：

```bash
.venv/bin/python macos/launcher.py stop
```

## 启动失败时

应用会弹出错误说明。运行日志在：

```text
~/Library/Logs/Next/server.log
```

在 Finder 按 **Command-Shift-G**，输入上面的路径即可找到日志。启动检查不调用 Todoist 或 AI，也不读取 API Key；没有新增凭证文件。

常见情况：

| 情况 | 处理方法 |
| --- | --- |
| 端口被其他程序占用 | 启动器会提示冲突，保留那个程序。确认占用程序后正常退出它；或退出 Next，在现有 `~/Library/Application Support/Next/config.json` 中将 `port` 改成空闲端口，再双击图标。 |
| 虚拟环境或依赖缺失 | 按项目主 README 的环境安装步骤修复 `.venv`。Keychain 中原来的凭证不用重填。 |
| 配置文件格式错误 | 检查现有 `config.json` 的 JSON 格式和端口值，修正后再打开。 |
| 项目被移动或重命名 | 恢复原路径，或按下方方法重新生成图标。 |
| 服务器运行但浏览器没有打开 | 手动访问打印/弹窗中的本机地址，并检查 macOS 的默认浏览器设置。 |
| 页面能打开，但提示 Todoist 拒绝令牌 | 这是 Todoist 授权问题，与服务器是否依赖 Terminal 无关。Todoist 的 API Token 和 AI 提供商的 API Key 是两条独立凭证；图标和启动器更新不会修改它们。 |

保留原有 Terminal 启动方式作为备用：

```bash
source .venv/bin/activate
python -m next_app
```

## 重启 Mac 后

仍然双击“应用程序”里的 Next 或点击 Dock 中的 Next 即可。此方案不添加登录启动项，也不安装常驻的 launchd 服务。每次按需启动；旧的状态文件不会被当作进程身份，文件锁会由系统在进程退出或重启时释放。

**请保留项目和 `.venv`。** 这是连接现有本地项目的轻量启动器，并不是把整个 Python 环境复制进 `.app` 的独立安装包。

## 重新生成应用

如果项目位置改变，在新项目目录中正常退出 Next 后运行：

```bash
.venv/bin/python macos/build_app.py --output /Applications/Next.app
```

只会覆盖具有本启动器 bundle ID 的 Next.app；不会覆盖同名的其他应用。构建使用 macOS 自带的 `osacompile`、`sips`、`iconutil` 和 `codesign`，不增加 Python 依赖。应用使用与网页相同的绿色箭头标记。

## 实现与验证

- `Next.applescript`：原生、保持运行的 AppleScript 应用，处理双击、重复打开、服务恢复和退出。退出前停止恢复检查，避免刚关闭的服务又被启动。
- `launcher.py`：标准库实现的后台启动和就绪检查。使用两个文件锁处理并发和单实例，私有 FIFO 发送退出请求；后台管理进程只终止其持有子进程句柄的 Next，不使用端口或 PID 文件定位后结束进程。
- `~/Library/Application Support/Next Launcher/`：启动锁、私有控制管道及诊断用 PID/端口信息；不含凭证。PID 信息只用于诊断，不用于终止进程。
- `build_app.py`：构建带图标、使用本机临时签名的 AppleScript 应用。移除模板的 `CFBundleIconName = applet`，让自定义 ICNS 图标生效。不会修改 Gatekeeper、系统安全设置或 Keychain。

自动化回归检查：

```bash
.venv/bin/python -m pytest -q
```

实际后台生命周期检查（需要已登录 macOS 桌面）：

```bash
.venv/bin/python tests/mac_launcher_smoke.py
```

该检查使用独立的临时目录和空闲测试端口运行真实 Next，验证冷启动、并发重复打开、正常退出、重新启动、端口冲突和启动失败；不读取 Todoist，不调用 AI，不修改正式配置。真实 Mac 重启未作为测试操作执行。

修复版还通过了实际应用检查：Terminal 应用已关闭、8765 无服务时，从 `/Applications/Next.app` 冷启动；重复打开仍只有一个服务器；退出 Next 后服务器正常停止；复用受控终端服务后，关闭该终端，Next 自动恢复后台服务。图标核对采用 macOS `NSWorkspace` 和正在运行应用实际提供的图像，而不只查看源 PNG。

原生 applet 行为参考：[AppleScript 事件处理说明](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/conceptual/ASLR_about_handlers.html)。
