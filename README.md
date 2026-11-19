# 私人电台 · Personal Radio

面向 Windows 的私人电台。你可以搜索网易云音乐和 QQ 音乐，也可以对 DJ 说「来几首伤感说唱」，让它从你已同步的曲库里选歌。

[下载 Windows x64 安装包](https://github.com/THresh01d/Personal-Radio/releases/latest) · [发行许可与署名](./NOTICE.md)

当前发行版包含 Rainform 派生舞台，供非商业用途。

## 音乐与推荐

| 想做什么 | 在电台里怎么做 |
| --- | --- |
| 搜歌和点播 | 在线搜索同时查询网易云音乐和 QQ 音乐，支持单曲、歌手和专辑。可播放的结果可以加入当前队列。 |
| 听自己的歌单 | 登录网易云可同步歌单到本机并查看首页推荐歌单。登录 QQ 音乐可浏览个人歌单、选歌播放或整张加入队列。 |
| 让 DJ 推荐 | DJ 从本机曲库选歌。当前通过同步网易云歌单建立该曲库。QQ 音乐歌单和在线搜索结果用于手动点播与排队。 |

曲库同步后，基础推荐可以离线运行，无需 API Key。在线搜索、播放平台音源及云端服务需要联网，可播范围以平台和账号权限为准。需要接入云端 AI 时，可以在「AI 设置」选择服务，调用可能消耗该服务的额度。

播放器支持歌词、播放队列、可配置的 Windows 全局快捷键，以及随音乐变化的雨夜、雪夜和圣诞夜舞台。

## 安装和升级

1. 在发布页下载文件名以 `Win-x64-Setup.exe` 结尾的安装包。可以用 PowerShell 的 `Get-FileHash` 计算安装包的 SHA-256，与同页的 `SHA256SUMS.txt` 对照。安装器未签名。
2. 初次安装可以分别选择程序目录和用户数据目录。曲库、DJ 记忆、设置和播放状态保存在用户数据目录，默认是 `%APPDATA%\PersonalRadio`。登录凭据由本机管理。
3. 已安装的桌面版可从「总设置 → 检查更新」打开发布页。覆盖安装前先退出电台，沿用原程序目录、用户数据目录和 Windows 账户。需要迁移资料时，可在向导里选择一个新目录或空文件夹，首次打开会复制并校验原数据。

## 从源码运行

准备 Windows x64、Python 3.12 x64 和 Node.js 24.16.0。在仓库根目录运行以下命令，第二条会打印本机访问地址。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1
```

如果电脑上有多个 Python 版本，给 `setup.ps1` 传入 `-Python`，指定 Python 3.12 的 `python.exe`。

源码运行时，可以用 `BGE_MODEL_PATH` 指定已下载的 BGE 模型目录。缺少本地模型时，首次需要向量检索的操作可能触发下载。

### 构建 Windows 安装包

公开安装包已经包含 Python、Node 和 BGE 检索模型。从源码打包时，下载 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，完成上述初始化，再在新的构建目录执行以下命令。将示例模型路径替换为你的目录。

```powershell
npm --prefix desktop ci --cache .cache/npm
.\.venv\Scripts\python.exe .\desktop\stage.py --model 'D:\models\bge-small-zh-v1.5'
npm --prefix desktop run package:win
```

安装包输出到 `desktop/dist`，文件名带 `Noncommercial`。如需预览桌面界面，可在暂存完成后执行 `npm --prefix desktop run start`。

如遇依赖下载失败，可用 `npm config get registry` 查看当前镜像，并用 `npm ping --registry=https://registry.npmjs.org/` 检查官方源是否可达。Electron、NSIS 或 Python 依赖的下载失败，可按构建日志中的地址检查代理和网络。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run test:player
npm --prefix desktop test
node --test "gateway/*.test.js"
```

## 许可与署名

Personal Radio 的原创代码使用 [MIT](./LICENSE)。舞台改编自 Rainform / 数据成雨，原作者为 afterimage。Rainform 派生组件沿用 [PolyForm Noncommercial 1.0.0](./frontend/src/components/scene/musicRain/rainform/LICENSE)，包含该组件的安装包供非商业使用。第三方运行依赖及 BGE 模型各自沿用原许可。[发行说明与署名](./NOTICE.md)
