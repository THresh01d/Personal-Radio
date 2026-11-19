# 私人电台 · Personal Radio（非商业发行）

本发行版包含雨夜、雪夜和圣诞夜，供非商业用途。雨幕渲染改编自 [Rainform / 数据成雨](https://github.com/afterimage-lab/Rainform)，原作者为 afterimage。发行范围和署名见 [NOTICE.md](./NOTICE.md)。

在自己的网易云曲库里点歌。可以说「想听中文民谣」，让 DJ 选择歌曲、介绍曲目，也可以直接浏览歌单和管理播放队列。

## Windows 使用方式

1. 从本仓库 Releases 下载 Windows x64 的 `Setup.exe`。在安装向导分别选择程序安装目录和用户数据目录，安装后打开「Personal Radio」。
2. 在电台里用网易云音乐扫码，选择自己的歌单并同步。音源可用性由你的网易云账户和平台决定。
3. 无需 API Key 即可使用基础推荐。需要云端 DJ 时，在「AI 设置」选择服务，输入自己的 Key，再保存。测试连接可能消耗所选服务额度。

安装包包含 Python、Node 和本地检索模型 BGE。扫码、同步、在线音源以及部分 DJ 服务需要联网。曲库、记忆、配置和播放状态保存在选定的用户数据目录。默认目录是 `%APPDATA%\PersonalRadio`。凭据保存在系统凭据库。

## 安装新版

在桌面应用右上角打开「总设置」，其中可以查看版本并检查更新。发现新版时，应用会提供本仓库的 GitHub 发布页入口。退出电台后运行新版安装包即可覆盖安装。在同一 Windows 账户下，原曲库与设置会继续使用。

升级向导会预填当前的安装和数据位置。需要换盘时，可为数据选一个新目录或空文件夹。首次打开会复制并校验原有数据，校验通过后使用新位置，原目录留作备份。

## 从源码运行

准备 Windows x64、Python 3.12 x64 和 Node.js 24.16.0，并确认 `python`、`node` 和 `npm` 都能在终端执行。在项目根目录用系统自带的 Windows PowerShell 5.1 安装依赖并构建前端。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
```

如果电脑上有多个 Python 版本，用 `-Python` 指定 Python 3.12 的 `python.exe` 路径。开发时运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1`，终端会显示本机访问地址。

## 构建桌面安装包

下载 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) 模型文件到单独目录。运行过上面的 setup 后，在新的构建目录运行以下命令。将模型路径换成你的目录。

```powershell
npm --prefix desktop ci --cache .cache/npm
./.venv/Scripts/python.exe desktop/stage.py --model 'D:/models/bge-small-zh-v1.5'
npm --prefix desktop run package:win
```

`desktop/stage.py` 准备安装包里的程序、模型和运行时。共用的运行时依赖收集、文件复制和 Node 许可证放在 `scripts/packaging/`。如需预览桌面界面，可在暂存完成后运行 `npm --prefix desktop run start`。

安装包输出到 `desktop/dist`。每个正式版同步更新 `desktop/package.json` 与锁文件的版本号，并在 `desktop/release-target.json` 设置形如 `"组织或用户名/仓库名"` 的 `repository`。使用与版本号对应的 GitHub 标签，例如 `v0.1.0`，将安装包及 SHA-256 校验文件上传到正式 Release。开发包未配置仓库时会提示尚未关联。

### 依赖下载失败

如果 `npm ci` 报连接错误，先用 `npm config get registry` 确认当前下载源，再用 `npm ping --registry=https://registry.npmjs.org/` 检查能否连到官方源。官方源可达时，可在当前 PowerShell 窗口切换后重试，继续使用仓库提供的锁文件和 `.cache/npm` 缓存。

```powershell
$env:npm_config_registry = 'https://registry.npmjs.org/'
```

如果日志显示失败的是 Electron 或 NSIS 下载，按日志里的目标地址检查网络和代理。Python 安装失败时，先确认 setup 使用的是 Python 3.12 x64，再检查 pip 配置的下载源是否可达。

## 验证

```powershell
./.venv/Scripts/python.exe -m pytest -q
npm --prefix desktop test
npm --prefix frontend run test:player
node --test gateway/*.test.js
```

Personal Radio 的原创代码使用 MIT 许可。Rainform 及其改编渲染代码使用 [PolyForm Noncommercial 1.0.0](./frontend/src/components/scene/musicRain/rainform/LICENSE)。此 Windows 安装包包含该组件，按非商业用途发行。第三方运行依赖及 BGE 模型各自沿用原许可。
