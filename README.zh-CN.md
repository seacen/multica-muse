# multica-muse

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.x](https://img.shields.io/badge/python-3.x-blue.svg)](https://www.python.org/)
[![Works with Multica](https://img.shields.io/badge/works%20with-Multica-orange)](https://github.com/multica-ai/multica)
![Delegates to Muse](https://img.shields.io/badge/delegates%20to-Muse-7c3aed)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/seacen/multica-muse/pulls)

**[English](README.md)**

**给你的 Multica 智能体配一个私人 AI。**

Multica 智能体只能跑代码 CLI。有了 multica-muse，它们可以把任务交给你的 Muse 应用——那个有浏览器、有工具、有记忆的 AI。

## 为什么有用

- Multica 智能体很强，但只能跑你给它的 CLI。
- 你的 Muse 应用能做更多：shell、浏览器、文件、记忆，还能跨工具判断。
- multica-muse 把两者连起来。几分钟装好。

## 工作原理

```
Multica server
    │  派发任务
    ▼
multica daemon（muse backend，Go）
    │  POST /v1/execute
    ▼
receptionist（Python，跑在你的 Muse 云电脑上，127.0.0.1:8765）
    │  任务入队
    ▼
queue watcher（事件 hook，每 5 秒检查一次）
    │  唤醒 worker
    ▼
worker（你的 Muse 应用）
    │  用完整能力执行
    ▼
结果沿原路返回 → 出现在 Multica 里
```

receptionist 只有一个 Python 文件。只用标准库。没有依赖要装。

## 快速开始

**方案 A —— 用 Skill（推荐）。**

```bash
npx skills add seacen/multica-muse@multica-muse-setup
```

然后跟你的 Muse 说："装一下 multica-muse。"
[Skill](skills/multica-muse-setup/SKILL.md) 会搞定一切：装接待员、登录
daemon、注册 queue watcher、跑烟雾测试，并建一个专属 side chat——以后
所有任务通知都发到那儿。

**方案 B —— 手动。**

**1. 运行安装脚本。**

```bash
git clone https://github.com/seacen/multica-muse.git
cd multica-muse
MULTICA_TOKEN=mul_your_token_here ./install.sh --server https://your-multica-server
```

Multica 官方托管版用 `--saas` 代替 `--server`。先去 Multica 网页建 token：头像 → Settings → API Tokens。用环境变量传 token，不会进 shell 历史。

**2. 让你的 Muse 注册 queue watcher 并建 side chat。**

> "Register the multica-muse queue watcher hook, and create the dedicated side chat for task notifications."

这一步要 agent 工具。shell 脚本做不了。你的 Muse 会先 dry run（空队列，再放一个测试任务），通过后才启用 hook。

**3. 从 Multica 派任务。**

在 Multica 里建一个 `muse` runtime 的智能体。给它派任务。结果回到你的 Multica 聊天里。

## 任务流转

1. 任务到达 `muse` runtime 上的 Multica 智能体。
2. daemon 把 prompt POST 给你的 receptionist。
3. receptionist 入队。watcher 几秒内唤醒一个 Muse worker。
4. worker 用你的完整 Muse 能力执行任务。
5. 结果返回。你在 Multica 里看到它。

## 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MUSE_RECEPTIONIST_TOKEN` | *（必填）* | Bearer token。daemon 那边配同样的值。 |
| `MUSE_RECEPTIONIST_HOST` | `127.0.0.1` | 监听地址。保持 localhost。 |
| `MUSE_RECEPTIONIST_PORT` | `8765` | 监听端口。 |

健康检查：`curl http://127.0.0.1:8765/healthz` 返回 `{"ok":true}`。

daemon 从 `~/.config/multica-muse/daemon.env` 读 `MUSE_ENDPOINT` 和 `MUSE_TOKEN`。安装脚本会帮你写好这个文件。

## 排障

- **hook 一直不触发。**hook 要 `hooks.enable`，而且 dry run 必须两个分支都过（空队列 → silent，测试任务 → wake）。让你的 Muse 查状态。
- **登录报 "unknown flag"。**新版 daemon 用 `multica login --token`，不是 `multica auth login --token`。
- **daemon 连不上 receptionist。**查 `~/.config/multica-muse/daemon.env` 里的 `MUSE_ENDPOINT`。host 和端口要对上。
- **Muse 云电脑没有 systemd。**用 `./start.sh` 后台启动 receptionist，不用 service 文件。

## 贡献

欢迎 PR。改动大先开 issue。

这个仓库是安装脚本和本机侧。Multica server 侧配套的 `muse` backend 在 [Multica 仓库](https://github.com/multica-ai/multica/pull/9155)。

## 许可证

MIT。见 [LICENSE](LICENSE)。
