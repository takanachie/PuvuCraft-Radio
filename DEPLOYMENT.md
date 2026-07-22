# PuvuCraft Radio 部署指南

本文档描述 PuvuCraft Radio 在单台 Ubuntu 24.04 Linux 服务器上的生产部署方式。生产架构为 Nginx、FastAPI、SQLite、FFmpeg 和 systemd，不使用 Docker。

## 1. 部署结构

生产请求链路：

```text
浏览器 -> HTTPS Nginx -> Vue 静态文件
                    -> FastAPI API / SSE
                    -> 经 auth_request 保护的 HLS 文件

FastAPI -> 每个活动频道一个持续 FFmpeg HLS 编码器
        -> SQLite 数据库
        -> 本地媒体库
```

固定路径：

| 路径 | 用途 |
| --- | --- |
| `/opt/radio` | 应用程序与 Python 虚拟环境 |
| `/opt/radio/config.yaml` | 生产配置 |
| `/opt/radio/data` | SQLite、媒体、封面和运行数据 |
| `/etc/radio/radio.env` | 服务环境变量和密钥 |
| `/etc/nginx/sites-available/radio` | Nginx 站点配置 |
| `/etc/systemd/system/radio.service` | systemd 服务 |

FastAPI 只监听 `127.0.0.1:8000`。公网只能访问 Nginx 的 80 和 443 端口。

## 2. 部署前准备

需要准备：

- 一台 Ubuntu 24.04 服务器。
- 指向服务器公网地址的域名，例如 `radio.example.com`。
- 安全组或防火墙允许 TCP 80 和 443。
- 至少 2 个 CPU 核心；频道较多时建议 4 核以上。
- 足够存放原始音乐、封面和备份的磁盘空间。
- 可持续提供所有在线听众总带宽的公网带宽。

每个频道都会持续转码。5 个 AAC 192 kbps 频道通常需要预留多个 CPU 核心。50 名听众仅音频下行约为 9.6 Mbps，尚未计入协议开销。

## 3. 安装系统依赖

```bash
sudo apt update
sudo apt install python3 python3-venv python3-dev build-essential \
  ffmpeg nginx sqlite3 acl certbot rsync
```

确认 FFmpeg 可用：

```bash
ffmpeg -version
ffprobe -version
```

创建不可登录的服务账号和程序目录：

```bash
sudo useradd --system --user-group --home-dir /opt/radio \
  --shell /usr/sbin/nologin radio
sudo install -d -o root -g root -m 0755 /opt/radio
```

如果 `radio` 用户已经存在，不要重复执行 `useradd`。

## 4. 构建和安装应用

前端要求 Node.js 22，版本记录在 `frontend/.nvmrc`。建议在开发机或独立构建环境中完成构建，再把审核后的项目发布到 `/opt/radio`。

```bash
cd frontend
npm ci
npm test
npm run build
cd ..
```

发布内容必须包含：

- `backend/`
- `frontend/dist/`
- `deploy/`
- `alembic.ini`
- `config.example.yaml`
- `pyproject.toml`
- `README.md`
- `SPEC.md`
- `DEPLOYMENT.md`

不要发布 `.venv`、`node_modules`、本地 `data`、缓存或开发配置。

将发布内容放入 `/opt/radio` 后安装 Python 环境：

```bash
sudo python3 -m venv /opt/radio/.venv
sudo /opt/radio/.venv/bin/pip install --upgrade pip
sudo /opt/radio/.venv/bin/pip install /opt/radio
sudo chown -R root:root /opt/radio/backend /opt/radio/frontend \
  /opt/radio/deploy /opt/radio/.venv
```

应用代码、前端产物和虚拟环境不应允许 `radio` 用户写入。

## 5. 创建运行目录

```bash
sudo install -d -o radio -g radio -m 0750 \
  /opt/radio/data \
  /opt/radio/data/media \
  /opt/radio/data/covers \
  /opt/radio/data/import \
  /opt/radio/data/tmp \
  /opt/radio/data/tmp/uploads \
  /opt/radio/data/runtime \
  /opt/radio/data/runtime/hls \
  /opt/radio/data/logs
```

Nginx 只需要读取 HLS 运行目录，不需要读取数据库和原始媒体库：

```bash
sudo setfacl -m u:www-data:--x /opt/radio/data /opt/radio/data/runtime
sudo setfacl -R -m u:www-data:rX /opt/radio/data/runtime/hls
sudo setfacl -m d:u:www-data:r-x,d:m::r-x \
  /opt/radio/data/runtime/hls
```

## 6. 配置应用

安装生产配置和环境变量文件：

```bash
sudo install -o root -g radio -m 0640 \
  /opt/radio/deploy/config.production.example.yaml \
  /opt/radio/config.yaml
sudo install -d -o root -g radio -m 0750 /etc/radio
sudo install -o root -g radio -m 0640 \
  /opt/radio/deploy/radio.env.example \
  /etc/radio/radio.env
```

生成服务密钥：

```bash
openssl rand -hex 32
sudoedit /etc/radio/radio.env
```

把生成值写入 `RADIO_SECRET_KEY`。不要把真实密钥写入项目、聊天、日志或工单。

编辑应用配置：

```bash
sudoedit /opt/radio/config.yaml
```

至少修改：

- `app.public_base_url`：替换为实际 HTTPS 域名。
- `app.timezone`：替换为实际显示时区。
- `media.import_directories`：确认只包含受信任目录。
- `ffmpeg.binary` 和 `ffmpeg.ffprobe_binary`：确认与系统路径一致。
- `auth.session.secure_cookie`：生产环境必须保持 `true`。

配置中的相对路径按配置文件目录解析。生产示例默认使用绝对路径。

## 7. 初始化数据库

生产服务不会自动创建或猜测数据库结构，必须先执行 Alembic：

```bash
sudo -u radio /bin/sh -c '
  set -a
  . /etc/radio/radio.env
  set +a
  cd /opt/radio
  exec .venv/bin/alembic -c alembic.ini upgrade head
'
```

每次部署包含数据库变更的新版本时，都要在启动应用前执行该命令。

## 8. 配置域名和 HTTPS

先编辑临时 HTTP 配置中的 `radio.example.com`：

```bash
sudo install -d -o root -g root -m 0755 /var/www/letsencrypt
sudo install -o root -g root -m 0644 \
  /opt/radio/deploy/radio-http-bootstrap.nginx.conf \
  /etc/nginx/sites-available/radio
sudoedit /etc/nginx/sites-available/radio
sudo ln -s /etc/nginx/sites-available/radio \
  /etc/nginx/sites-enabled/radio
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/letsencrypt \
  -d radio.example.com
```

如果默认站点冲突，可以删除 `/etc/nginx/sites-enabled/default` 的符号链接，但不要删除发行版原始配置文件。

安装最终 HTTPS 配置，并替换其中所有 `radio.example.com`：

```bash
sudo install -o root -g root -m 0644 \
  /opt/radio/deploy/radio.nginx.conf \
  /etc/nginx/sites-available/radio
sudo install -o root -g root -m 0755 \
  /opt/radio/deploy/certbot-reload-nginx \
  /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
sudoedit /etc/nginx/sites-available/radio
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

Nginx 会执行以下安全策略：

- 所有 HLS 清单和分片都通过 FastAPI 会话授权。
- 大型音乐和封面请求在接收请求体前验证管理员会话。
- 普通 API 请求体限制为 1 MiB。
- FastAPI、SQLite 和原始媒体目录不直接暴露到公网。
- HTTP 自动跳转到 HTTPS。

## 9. 启动 systemd 服务

```bash
sudo install -o root -g root -m 0644 \
  /opt/radio/deploy/radio.service \
  /etc/systemd/system/radio.service
sudo systemctl daemon-reload
sudo systemctl enable --now radio.service
sudo systemctl status radio.service
```

查看日志：

```bash
sudo journalctl -u radio.service -f
sudo tail -f /opt/radio/data/logs/radio.log
sudo tail -f /var/log/nginx/radio.error.log
```

确认 Uvicorn 只监听回环地址：

```bash
ss -lntp | grep 8000
```

预期地址为 `127.0.0.1:8000`，不能是 `0.0.0.0:8000`。

## 10. 创建首个管理员

首次启动后会生成：

```text
/opt/radio/data/bootstrap.token
```

仅在服务器终端读取该文件，然后访问：

```text
https://radio.example.com/setup
```

向网页输入令牌并创建管理员。成功后应用会自动删除令牌文件并永久关闭初始化入口。

不要把一次性令牌发送到聊天、截图、日志或工单。

## 11. 部署验证

```bash
sudo nginx -t
sudo systemctl is-active radio.service nginx
sudo systemd-analyze security radio.service
curl -I https://radio.example.com/
curl -I https://radio.example.com/hls/nonexistent.m3u8
```

匿名访问 HLS 必须得到 `401` 或 `403`，不能返回 SPA 页面或媒体文件。

登录后应验证：

1. 管理员可以上传音乐并读取自动提取的元数据。
2. 管理员可以创建频道、维护歌单和切歌。
3. 普通用户注册后必须等待管理员审批。
4. 两个浏览器进入同一频道时听到相同歌曲和近似进度。
5. 停用用户后，其 API、SSE 和后续 HLS 分片访问会失效。

## 12. 备份

SQLite 的 `.backup` 命令可以安全备份正在使用 WAL 的数据库：

```bash
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o root -g radio -m 0750 /var/backups/radio
sudo install -d -o radio -g radio -m 0700 \
  "/var/backups/radio/$stamp"
sudo -u radio sqlite3 /opt/radio/data/radio.db \
  ".backup '/var/backups/radio/$stamp/radio.db'"
sudo rsync -a /opt/radio/data/media/ \
  "/var/backups/radio/$stamp/media/"
sudo rsync -a /opt/radio/data/covers/ \
  "/var/backups/radio/$stamp/covers/"
sudo sqlite3 "/var/backups/radio/$stamp/radio.db" \
  "PRAGMA integrity_check;"
```

完整性检查必须输出 `ok`。备份应加密并复制到服务器外部，设置保留周期并定期演练恢复。

不需要备份：

- `/opt/radio/data/runtime/hls`
- `/opt/radio/data/tmp`
- 日志缓存

## 13. 恢复

恢复数据库和媒体前先停止服务：

```bash
sudo systemctl stop radio.service
```

恢复 `radio.db`、`media/` 和 `covers/`，然后执行：

```bash
sudo chown -R radio:radio /opt/radio/data
sudo -u radio /bin/sh -c '
  set -a
  . /etc/radio/radio.env
  set +a
  cd /opt/radio
  .venv/bin/alembic -c alembic.ini upgrade head
'
sudo systemctl start radio.service
```

## 14. 升级

建议升级顺序：

1. 创建数据库和媒体备份。
2. 在构建环境运行后端测试、前端测试和生产构建。
3. 停止 `radio.service`。
4. 替换 `/opt/radio` 中的应用代码和前端产物。
5. 更新 Python 虚拟环境依赖。
6. 执行 `alembic upgrade head`。
7. 启动服务并检查日志、首页、登录和 HLS。

不要在未备份数据库时执行无法确认影响的迁移。

## 15. 常见问题

### 频道显示 offline

检查：

```bash
sudo -u radio /usr/bin/ffmpeg -version
sudo journalctl -u radio.service -n 200 --no-pager
ls -l /opt/radio/data/media
```

常见原因包括歌单为空、音乐文件不可读、FFmpeg 路径错误或磁盘已满。

### HLS 返回 401 或 403

未登录时这是预期行为。已登录仍失败时，检查会话 Cookie、HTTPS、安全 Cookie 配置和 `/api/internal/stream-auth` 的后端日志。

### 上传返回 413

确认请求是否走最终 Nginx 配置。音乐上传位置允许 500 MiB 文件及 multipart 开销，其他 API 仍限制为 1 MiB。

### SQLite database is locked

确认只运行一个 Uvicorn worker 和一个 `radio.service` 实例，并检查数据库是否位于本地磁盘而不是网络文件系统。

### 跨境访问不稳定

优先检查客户端到服务器的路由、云防火墙和运营商网络。HLS 使用多个短请求，持续丢包会导致缓冲。必要时使用更合适的地域或合规 CDN，但 CDN 接入必须继续保护私有 HLS。
