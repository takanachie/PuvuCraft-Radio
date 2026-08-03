# PuvuCraft Radio

PuvuCraft Radio 是基于 FastAPI、Vue 3、FFmpeg 和 HLS 的多频道同步在线音乐电台。完整产品与技术约束见 [`SPEC.md`](SPEC.md)。

> [!IMPORTANT]
> 本项目主要由 AI 生成，用于个人学习和自托管使用。代码按现状提供，部署者应在公开服务前自行完成安全审查、版权确认和运行环境加固。

## 当前能力

- 用户注册、管理员审批、Argon2id 密码和可撤销 Cookie 会话
- 一次性令牌保护的首次管理员网页向导
- 频道、音乐库、歌单和播放控制后台
- 所有管理员可见的 10 位持久化上传队列、并行传输和断页清理
- ffprobe 验证、标签与封面提取，以及超规格音频的 FLAC 规范化
- 基于优先级和磁盘使用率上限的多挂载媒体存储
- 每频道按需共享的 320 kbps AAC/HLS 编码器、持续逻辑时间线、切歌和重启恢复
- 带 30 天连接窗口、单连接接管和空 404 防泄露的外部播放器持续流；管理员可选 FLAC
- Vue 3 深色电台控制台、hls.js 播放器和 SSE 状态更新
- Android 9+ 原生 Kotlin 客户端，支持登录、自适应频道界面、AAC/管理员 FLAC 与后台收听
- Nginx `auth_request`、HTTPS 和 systemd 部署模板

## 开发环境

需要 Python 3.12+、Node.js 22+、FFmpeg 和 ffprobe。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp config.example.yaml config.yaml
export RADIO_SECRET_KEY="$(openssl rand -hex 32)"
.venv/bin/uvicorn backend.app.main:app --reload
```

另一个终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

首次启动后，从 `data/bootstrap.token` 读取一次性令牌并访问 `/setup`。成功创建管理员后，该令牌文件会自动删除。

## 验证

```bash
.venv/bin/ruff check backend tests
.venv/bin/pytest
cd frontend && npm test && npm run build
```

本地缺少 FFmpeg 时，账号和管理 API 仍可启动，但频道会显示 `offline` 并报告 FFmpeg 不可用。

## Android 客户端

Android 客户端代码位于 [`app/`](app/)，只提供登录和收听功能。它支持 Android 9（API 28）及以上系统，构建、屏幕适配与安全说明见 [`app/README.md`](app/README.md)。

## 生产部署

完整生产部署步骤见 [`DEPLOYMENT.md`](DEPLOYMENT.md)，所需模板位于 `deploy/`。生产环境必须使用 HTTPS、安全 Cookie、独立服务账号，并通过 Nginx 对每个 HLS 清单和分片执行会话授权。
