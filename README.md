# S.C.H.A.L.E. Course Suite（蔚蓝档案主题三平台网课助手）

一套将 **超星学习通（多账号并发）**、**UOOC 优课在线**、**知道智慧树** 整合在统一架构中的跨平台 Web 控制台与桌面客户端，支持 **Linux 服务器 / Docker / 软路由常驻** 与 **Windows 独立桌面端（免安装 Python）**。

> [!IMPORTANT]
> **合规与使用边界说明**：本项目仅用于学习辅助与视频/音频/文档/阅读类常规进度播放，**不提供**自动完成评分测验/考试/作业、自动刷讨论区发言、自动过验证码或定位作弊功能。测验与讨论请通过页面内置的官网直达入口与「联网解析与讨论思路助手」自行完成。

---

## ✨ 核心特性

### 1. 三平台 × 三位《蔚蓝档案》角色动效 UI（均支持日/夜双模式切换）

| 平台 | 默认端口 | 专属角色主题 | 日 / 夜双模式 | 核心能力 |
| :--- | :---: | :--- | :--- | :--- |
| **超星学习通** | `51092` | **阿罗娜 & 普拉娜**（S.C.H.A.L.E.） | ☀️ 阿罗娜晴空蓝白 ⇄ 🌙 普拉娜暗夜紫粉 | **多账号独立线程并发**、扫码 / 密码 / Cookie 三登录、视频/音频/文档/阅读、`enc` 动态签名、自动跳过评分测验 |
| **UOOC 优课在线** | `51088` | **圣园未花 Mika**（圣三一茶话会） | 🌸 圣三一樱粉星穹 ⇄ 🌌 银河星夜 | Cookie 持久化、时间步进防拖拽退避（自动降速 1.25x/1.0x）、纯 Python 视频时长探测（Windows 免装 `ffmpeg`）、未完成测验/作业提醒徽章 |
| **知道智慧树** | `51089` | **才羽绿 & 才羽桃井**（游戏开发部 GDD） | 🎮 小绿翠绿电玩 ⇄ 🕹️ 小桃霓虹像素夜 | 扫码一键登录 / Cookie 导入、共享学分课（Zhidao）+ 校内学分课（Hike）、WatchPoint AES 加密、随堂弹题处理与拟人微休 |

- 每套主题均内置 **旋转光环 SVG 徽标**、**交互式粒子背景 Canvas**、**点击光环/星芒/像素涟漪动效**、**BA 斜切流光按钮**、**动态能量进度条** 与 **桃信风格 Toast 提示**。
- 页面顶部三平台按钮自动根据当前访问域名/IP 拼接端口，无论 `localhost`、局域网还是公网 VPS 均可直接互跳。

### 2. 三平台共用「联网学习与讨论助手」
- 免 API Key 即可使用 **必应搜索 RSS + 百度百科** 免费联网查资料。
- 支持自填任意 **OpenAI 兼容 API Key**（内置 DeepSeek、通义千问、Kimi、硅基流动、OpenAI 预设），配置持久化于 `data/shared_ai_config.json`，三平台实时共用。

### 3. 隐私与零数据残留设计
- 所有账号 Cookie、多账号会话、AI Key 与运行日志统一存放在运行期生成的 `data/` 目录中（已写入 `.gitignore`），源码仓库与发布包中**不含任何个人账号数据**。

---

## 🚀 快速部署指南

### 方式一：Windows 桌面端（免安装 Python）

1. 下载并解压 `SCHALE-Course-Suite-Windows-x64.zip`。
2. 双击运行 **`SCHALE-Course-Suite.exe`**，弹出夏莱桌面控制面板并自动启动三个本地服务。
3. 点击对应角色卡片上的 **「🌐 打开控制台页面」** 即可在浏览器中使用；关闭桌面窗口会自动停止全部服务。

> 如果想从源码在 Windows 运行：确保已安装 Python 3.10+，直接双击 `windows/start_windows.bat`。
> 如果想自己重新打包 `.exe`：双击 `windows/build_exe.bat` 即可生成 `dist/SCHALE-Course-Suite.exe`。

---

### 方式二：Linux 一键安装与后台常驻（推荐 VPS / 树莓派 / ARM64 / x86_64）

```bash
tar -xzf schale-course-suite-linux.tar.gz
cd schale-course-suite

# 自动创建 .venv 并安装依赖（root 环境会询问是否注册 systemd 开机自启服务）
bash linux/install.sh

# 手动前台启动
./linux/start.sh

# 或后台守护启动 / 停止
./linux/start.sh daemon
./linux/start.sh stop
```

自定义监听地址、端口或仅启动部分平台：

```bash
python3 server.py --host 0.0.0.0 --uooc-port 51088 --zhs-port 51089 --cx-port 51092
# 仅启动超星学习通多账号版：
python3 server.py --only cx --cx-port 51092
```

---

### 方式三：Docker / Docker Compose 一键部署

```bash
docker compose up -d --build
```

启动后访问：
- 超星学习通（阿罗娜/普拉娜）：`http://<你的IP>:51092`
- UOOC 优课在线（圣园未花）：`http://<你的IP>:51088`
- 知道智慧树（小绿&小桃）：`http://<你的IP>:51089`

---

## 📁 目录结构

```text
schale-course-suite/
├── server.py                      # 统一跨平台服务端入口（单进程驱动三端）
├── win_gui.py                     # Windows 原生图形桌面客户端
├── core/
│   ├── paths.py                   # 跨平台与 PyInstaller 资源/数据目录解析
│   └── study_assistant.py         # 三平台共享联网检索 + AI 解析模块
├── platforms/
│   ├── cx/                        # 超星学习通多账号并发引擎 + 阿罗娜/普拉娜 UI
│   ├── uooc/                      # UOOC 优课引擎 + 圣园未花 UI
│   └── zhs/                       # 知道智慧树引擎 + 才羽绿/才羽桃井 UI
├── linux/                         # Linux install.sh / start.sh / systemd 模板
├── windows/                       # Windows start_windows.bat / build_exe.bat
├── Dockerfile & docker-compose.yml
├── requirements.txt
├── LICENSE
└── THIRD_PARTY_LICENSES.md
```

---

## ⚠️ 免责声明与开源许可

- 本项目基于 [MIT License](LICENSE) 开源，第三方协议模块归属与致谢请见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
- 请遵守所在学校与平台的使用规范，本项目仅供技术交流与个人学习辅助，请勿用于任何违规用途；若暴露到公网服务器，建议配合防火墙 IP 白名单或反向代理加 Basic Auth 保护。
