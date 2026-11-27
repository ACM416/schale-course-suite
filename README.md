<div align="center">

# S.C.H.A.L.E. Course Suite

**多平台统一自托管网课学习控制台**

[![Release](https://img.shields.io/github/v/release/ACM416/schale-course-suite?color=128afa&style=flat-square)](https://github.com/ACM416/schale-course-suite/releases)
[![License](https://img.shields.io/github/license/ACM416/schale-course-suite?color=10b981&style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square&logo=python&logoColor=white)](requirements.txt)
[![Platforms](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20Docker-6366f1?style=flat-square)](#deploy)

整合 **超星学习通** · **UOOC 优课在线** · **知道智慧树**（三平台均支持**多账号独立线程并发**）<br/>
支持 **Windows 独立桌面端（免安装 Python/FFmpeg）** 与 **Linux / Docker / 软路由后台常驻**

[📦 下载发行版](https://github.com/ACM416/schale-course-suite/releases/latest) · [✨ 功能说明](#features) · [🚀 快速部署](#deploy) · [⚙️ 参数配置](#config) · [❓ 常见问题](#faq)

</div>

---

> [!IMPORTANT]
> **合规与使用边界说明**
> 本项目仅用于个人账号的**视频 / 音频 / 文档 / 阅读类常规学习进度辅助**与**学习入口导航**。
> 本项目**不提供**自动完成评分测验 / 考试 / 作业、自动刷讨论区发言、绕过验证码或定位作弊功能；评分测验与讨论请通过页面内置的官网直达入口及「联网学习与讨论助手」自行完成。

---

<a id="features"></a>

## ✨ 功能说明

### 1. 支持平台与核心能力

| 平台 | 默认端口 | 登录方式 | 核心功能 |
| :--- | :---: | :--- | :--- |
| **超星学习通** | `51092` | 扫码 / 账号密码 / Cookie | **多账号独立线程并发运行**、全部账号批量启动/暂停/停止、视频/音频/文档/阅读进度上报、`enc` 动态签名、自动跳过评分测验并标注待办入口 |
| **UOOC 优课在线** | `51088` | 多账号 Cookie 池 | **多账号独立线程并发**、批量全部启停、时间步进防拖拽退避（异常自动降速 1.25x / 1.0x）、纯 Python 视频时长探测（解析 MP4 `mvhd` 与 HLS `.m3u8`，无需 `ffmpeg`）、未完成测验/作业数量提醒 |
| **知道智慧树** | `51089` | 多账号扫码 / Cookie | **多账号独立线程并发**（线程隔离进度/日志/启停）、支持共享学分课（Zhidao）与校内学分课（Hike）、WatchPoint AES 加密上报、随堂弹题处理与拟人微休 |

### 2. 通用功能

- **单进程统一托管**：由 `server.py` 单进程驱动三个平台的 Web 控制台，支持通过 `--only` 按需只启动指定平台，资源占用低。
- **跨平台快捷跳转**：页面顶部自动根据当前访问的域名或 IP 拼接对应端口，支持在 `127.0.0.1`、局域网与公网环境直接互跳。
- **共享「联网学习与讨论助手」**：
  - **免 Key 联网检索**：内置必应中文 RSS 与百度百科 OpenAPI，不填 API Key 即可直接检索知识点资料。
  - **自定义 AI 接口**：支持配置任意 OpenAI 兼容 API（内置 DeepSeek、通义千问、Kimi、硅基流动、OpenAI 预设），配置持久化于 `data/shared_ai_config.json`，三平台实时共用。
- **官方任务直达入口**：课程卡片内置「练习/测验」「讨论区」「章节页」快捷链接，便于直接跳转到官网手动完成评分任务。
- **数据隔离与零残留**：所有登录态、多账号配置、AI Key 与日志统一保存在运行期生成的 `data/` 目录中（已加入 `.gitignore`），源码与发行包不含任何个人账号数据。

---

<a id="deploy"></a>

## 🛠️ 快速部署指南

### 方式一：Windows 独立桌面客户端（免安装 Python / FFmpeg）

1. 前往 [Releases 页面](https://github.com/ACM416/schale-course-suite/releases/latest) 下载 **`SCHALE-Course-Suite-Windows-x64.zip`** 并解压。
2. 双击运行 **`SCHALE-Course-Suite.exe`**，启动图形控制面板并自动拉起三个本地服务。
3. 点击对应平台的 **「打开控制台页面」** 在浏览器中使用；支持修改端口、一键开启局域网访问（`0.0.0.0`）和打开本地 `data/` 目录，关闭窗口时自动停止全部服务。

> [!TIP]
> 如需在 Windows 从源码运行或自行打包（需 Python 3.10+）：
> - **源码启动**：双击 `windows\start_windows.bat`（自动创建 `.venv` 并安装依赖）。
> - **自行打包 `.exe`**：双击 `windows\build_exe.bat`，输出路径为 `dist\SCHALE-Course-Suite.exe`。

---

### 方式二：Linux 一键安装与后台常驻（VPS / 树莓派 / 软路由 / ARM64 & x86_64）

```bash
# 1. 下载并解压 Linux 发行包（也可直接 git clone 本仓库）
curl -LO https://github.com/ACM416/schale-course-suite/releases/download/v1.0.0/schale-course-suite-linux.tar.gz
tar -xzf schale-course-suite-linux.tar.gz
cd schale-course-suite

# 2. 创建 .venv 虚拟环境并安装依赖
#    root 环境下若检测到 systemd，会提示是否注册为开机自启服务
bash linux/install.sh
```

启停管理：

```bash
# 前台运行
./linux/start.sh

# 后台守护运行（日志输出至 data/suite.log，PID 写入 data/suite.pid）
./linux/start.sh daemon

# 停止后台服务
./linux/start.sh stop
```

如已注册 `systemd` 服务，可使用系统命令管理：

```bash
systemctl status schale-course-suite
systemctl restart schale-course-suite
journalctl -u schale-course-suite -f
```

---

### 方式三：Docker / Docker Compose 部署

```bash
git clone https://github.com/ACM416/schale-course-suite.git
cd schale-course-suite
docker compose up -d --build
```

容器默认挂载 `./data:/app/data` 以持久化登录态与配置。启动后访问：
- 超星学习通：`http://<你的IP>:51092`
- UOOC 优课在线：`http://<你的IP>:51088`
- 知道智慧树：`http://<你的IP>:51089`

---

<a id="config"></a>

## ⚙️ 命令行参数与环境变量

`server.py` 支持通过命令行参数或环境变量配置（命令行参数优先）：

| 命令行参数 | 对应环境变量 | 默认值 | 说明 |
| :--- | :--- | :---: | :--- |
| `--host` | `SCHALE_HOST` | `0.0.0.0` | 监听地址（仅本机访问可设为 `127.0.0.1`） |
| `--uooc-port` | `UOOC_PORT` | `51088` | UOOC 优课在线监听端口 |
| `--zhs-port` | `ZHS_PORT` | `51089` | 知道智慧树监听端口 |
| `--cx-port` | `CX_PORT` | `51092` | 超星学习通多账号监听端口 |
| `--only` | — | 全部 (`uooc,zhs,cx`) | 逗号分隔，仅启动指定平台（如 `--only cx` 或 `--only uooc,cx`） |
| `--data-dir` | `SCHALE_DATA_DIR` | `./data` | 自定义持久化数据目录 |

示例：

```bash
# 自定义全部端口
python3 server.py --host 0.0.0.0 --uooc-port 8088 --zhs-port 8089 --cx-port 8092

# 仅启动超星学习通多账号服务并指定数据目录
python3 server.py --only cx --cx-port 51092 --data-dir /var/lib/schale-data
```

---

## 🔒 数据持久化与公网安全建议

运行期间生成的配置、登录态与日志均保存在 `data/` 目录（Windows `.exe` 位于程序同级 `data/` 目录）：

```text
data/
├── cx_accounts.json        # 超星学习通多账号会话与 Cookie
├── uooc_accounts.json      # UOOC 多账号会话与倍速/间隔配置（自动兼容迁移旧版 uooc_config.json）
├── zhs_accounts.json       # 智慧树多账号会话与 Cookie（自动兼容迁移旧版 zhs_cookies.json）
├── shared_ai_config.json   # 三平台共享的 AI API 配置
├── zhs_logs/               # 智慧树运行日志
└── suite.log / suite.pid   # Linux daemon 日志与进程 PID
```

> [!WARNING]
> **公网部署安全提示**
> 控制台默认不内置登录鉴权。若部署在公网服务器，建议采用以下至少一种防护方式：
> 1. 仅监听 `127.0.0.1`，通过 **Nginx / Caddy 反向代理 + Basic Auth + HTTPS** 对外访问；
> 2. 通过 **Tailscale / WireGuard / FRP 私有隧道** 访问；
> 3. 在防火墙或云安全组中配置 **来源 IP 白名单**。

---

## 📁 目录结构

```text
schale-course-suite/
├── server.py                       # 跨平台统一服务端入口（单进程驱动三平台）
├── win_gui.py                      # Windows Tkinter 桌面控制面板
├── core/
│   ├── paths.py                    # 源码 / PyInstaller 资源路径与 data/ 目录解析
│   └── study_assistant.py          # 共享联网检索（Bing/百科）与 OpenAI 兼容 AI 模块
├── platforms/
│   ├── cx/                         # 超星学习通多账号并发引擎与 Web 控制台
│   ├── uooc/                       # UOOC 优课引擎、任务调度与 Web 控制台
│   └── zhs/                        # 知道智慧树引擎与 Web 控制台
├── linux/
│   ├── install.sh                  # Linux 依赖安装与 systemd 服务注册脚本
│   ├── start.sh                    # 前台 / daemon 守护启停脚本
│   └── schale-course-suite.service # systemd 服务配置模板
├── windows/
│   ├── start_windows.bat           # Windows 源码一键启动脚本
│   └── build_exe.bat               # PyInstaller 独立 .exe 打包脚本
├── Dockerfile                      # Docker 镜像构建文件
├── docker-compose.yml              # Docker Compose 编排文件
├── requirements.txt                # Python 依赖清单
├── LICENSE                         # MIT 开源许可
└── THIRD_PARTY_LICENSES.md         # 第三方模块与依赖许可说明
```

---

<a id="faq"></a>

## ❓ 常见问题（FAQ）

<details>
<summary><b>1. Windows 运行 <code>SCHALE-Course-Suite.exe</code> 时被 SmartScreen 或杀毒软件拦截？</b></summary>

未签名的 PyInstaller 单文件可执行程序常会触发误报。项目全部源码公开，可自行审查后运行 `windows\build_exe.bat` 从源码重新编译打包。

</details>

<details>
<summary><b>2. 默认端口冲突如何修改？</b></summary>

- **Windows 桌面端**：直接在界面修改对应端口号，点击「停止全部服务」后重新「启动全部服务」。
- **Linux / 源码**：通过 `--uooc-port` / `--zhs-port` / `--cx-port` 参数或对应环境变量修改。
- **Docker**：调整 `docker-compose.yml` 中的 `ports` 与 `environment` 后重新执行 `docker compose up -d`。

</details>

<details>
<summary><b>3. 联网学习助手不配置 API Key 能否使用？</b></summary>

可以。未配置 Key 时默认通过必应中文 RSS 与百度百科 OpenAPI 免费检索资料；填入任意 OpenAI 兼容 API Key 后可开启 AI 总结与讨论思路辅助。

</details>

<details>
<summary><b>4. 为什么不提供自动答题或自动刷讨论功能？</b></summary>

为遵守平台规则与合规边界，项目仅限音视频/文档等常规学习进度辅助与学习工具，不提供自动答题、代写或自动刷讨论功能。

</details>

---

## 📄 开源许可与免责声明

- 本项目基于 [MIT License](LICENSE) 开源。
- 智慧树底层协议适配模块参考并基于 [VermiIIi0n/fuckZHS](https://github.com/VermiIIi0n/fuckZHS)（MIT License），详见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
- **免责声明**：本项目仅供学习交流与技术研究使用，请遵守所在学校与平台的相关规定，因不当使用产生的后果由使用者自行承担。
