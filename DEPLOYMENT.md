# PuvuCraft Radio 部署指南

本文档描述 PuvuCraft Radio 在单台 Ubuntu 24.04 Linux 服务器上的生产部署方式。生产架构为 Nginx、FastAPI、SQLite、FFmpeg 和 systemd，不使用 Docker。

## 1. 部署结构

生产请求链路：

```text
浏览器 -> HTTPS Nginx -> Vue 静态文件
                    -> FastAPI API / SSE
                    -> 经 auth_request 保护的 HLS 文件
外部播放器 -> HTTPS Nginx -> FastAPI 持续 AAC / FLAC 音频流

FastAPI -> 每个有听众频道一个共享 AAC/HLS FFmpeg 编码器
        -> 有管理员 FLAC 听众时额外启动共享 FLAC 编码器
        -> 无听众时只推进持久化逻辑时间线
        -> SQLite 数据库
        -> 公共上传队列与 FFmpeg 规范化
        -> 配置中的一个或多个媒体挂载
```

固定路径：

| 路径 | 用途 |
| --- | --- |
| `/opt/radio` | 应用程序与 Python 虚拟环境 |
| `/opt/radio/config.yaml` | 生产配置 |
| `/opt/radio/data` | SQLite、默认媒体、封面和运行数据 |
| `/opt/radio/tmp` | 上传与规范化临时文件，不备份 |
| `/etc/radio/radio.env` | 服务环境变量和密钥 |
| `/etc/nginx/sites-available/radio` | Nginx 站点配置 |
| `/etc/systemd/system/radio.service` | systemd 服务 |

FastAPI 监听 `0.0.0.0:8000`，以接收域名上游或外部反向代理的连接。生产流量仍应先经过 HTTPS 反向代理；安全组或防火墙应尽可能只允许可信上游访问 8000 端口。

## 2. 部署前准备

需要准备：

- 一台 Ubuntu 24.04 服务器。
- 指向服务器公网地址的域名，例如 `radio.example.com`。
- 安全组或防火墙允许 TCP 80 和 443；域名由外部上游转发时，还需允许该上游访问 TCP 8000。
- 至少 2 个 CPU 核心；频道较多时建议 4 核以上。
- 足够存放原始音乐、封面和备份的磁盘空间。
- 可持续提供所有在线听众总带宽的公网带宽。

只有存在听众的频道才会转码，最后一名听众离开 30 秒后停止编码。5 个同时活跃的 AAC 320 kbps 频道仍应预留多个 CPU 核心；50 名 AAC 听众仅音频下行约为 16 Mbps，尚未计入协议和 TLS 开销。管理员 FLAC 流的实际码率取决于音频内容，需另行预留带宽和 CPU。

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

不要发布 `.venv`、`node_modules`、本地 `data`、`tmp`、缓存或开发配置。

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
  /opt/radio/data/runtime \
  /opt/radio/data/runtime/hls \
  /opt/radio/data/logs \
  /opt/radio/tmp \
  /opt/radio/tmp/uploads \
  /opt/radio/tmp/normalized
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
- `uploads`：确认临时目录、10 位队列上限、并行数和心跳时限。
- `storage.locations`：为每个媒体挂载设置稳定唯一 ID、绝对根目录、优先级和最大磁盘使用百分比。
- `ffmpeg.binary` 和 `ffmpeg.ffprobe_binary`：确认与系统路径一致。
- `auth.session.secure_cookie`：生产环境必须保持 `true`。
- `player_api`：确认 30 天最晚连接窗口、5 秒客户端队列、10 秒连接接管时限和 FLAC 参数。

配置中的相对路径按配置文件目录解析。生产示例默认使用绝对路径。

应用不会代替操作系统挂载磁盘。额外磁盘应先通过 `/etc/fstab` 或等价机制挂载，再把其中的媒体根目录加入 `storage.locations`。`priority` 数值越大越优先；写入后预计磁盘占用超过 `max_usage_percent` 时，应用自动选择下一可用位置。生产配置建议保持 `create_if_missing: false`，防止挂载缺失时误写到系统盘。

每个额外挂载还必须加入 systemd 的挂载依赖与写入白名单。例如：

```ini
# sudo systemctl edit radio.service
[Unit]
RequiresMountsFor=/mnt/radio-secondary/media

[Service]
ReadWritePaths=/mnt/radio-secondary/media
```

对应目录必须由 `radio:radio` 拥有，并在修改后执行 `sudo systemctl daemon-reload`。SQLite 中保存的是存储 ID 和相对文件名，不保存绝对路径，因此在保持 ID 不变的情况下可以调整挂载根目录。

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
- `/listen` 持续音频流关闭代理缓冲与访问日志；无效凭据只返回空响应体 404。
- 大型音乐和封面请求在接收请求体前验证管理员会话；音乐请求关闭代理缓冲，以便页面关闭时立即中断上游传输。
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

确认 Uvicorn 在生产端口监听所有 IPv4 地址：

```bash
ss -lntp | grep 8000
```

预期地址为 `0.0.0.0:8000`。如果 8000 端口由外部反向代理访问，应在安全组或防火墙中限制其来源地址，避免绕过 HTTPS 入口。

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

1. 两个管理员页面能看到同一公共上传队列，队列满 10 项后拒绝新预约，服务器按配置并行开始传输。
2. 关闭持有任务的页面后，排队和传输中的任务过期，`/opt/radio/tmp` 中不留下对应文件。
3. 上传超过 48 kHz、2 声道或 32 bit 的测试音频后，曲目显示为 FLAC 规范化且最终参数不超过推流配置。
4. 管理员可以创建频道、维护歌单和切歌。
5. 普通用户注册后必须等待管理员审批。
6. 两个浏览器进入同一频道时听到相同歌曲和近似进度。
7. 停用用户后，其 API、SSE 和后续 HLS 分片访问会失效。
8. 在收听页复制 AAC 播放器连接后可持续播放；同一连接再次打开时旧连接会被替换。
9. 无效、过期或已刷新的播放器连接只得到空响应体 404，Nginx 与 Uvicorn 日志不出现完整连接地址。

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

上例只备份生产示例的 `primary` 存储。配置了多个 `storage.locations` 时，必须逐一备份每个启用位置，并保留其存储 ID 与备份目录的映射，否则恢复后的 SQLite 记录无法定位媒体文件。

不需要备份：

- `/opt/radio/data/runtime/hls`
- `/opt/radio/tmp`
- 日志缓存

## 13. 恢复

恢复数据库和媒体前先停止服务：

```bash
sudo systemctl stop radio.service
```

按原存储 ID 恢复 `radio.db`、每个媒体根和 `covers/`，确认所有挂载就绪，然后执行：

```bash
sudo chown -R radio:radio /opt/radio/data
sudo chown -R radio:radio /opt/radio/tmp
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
sudo -u radio test -w /opt/radio/data/media
```

常见原因包括歌单为空、SQLite 中的存储 ID 未配置、对应挂载缺失、音乐文件不可读、FFmpeg 路径错误或磁盘达到配置上限。

### HLS 返回 401 或 403

未登录时这是预期行为。已登录仍失败时，检查会话 Cookie、HTTPS、安全 Cookie 配置和 `/api/internal/stream-auth` 的后端日志。

### 外部播放器返回 404 或 503

`404` 故意不提供响应正文或具体原因。用户应回到收听页确认账号状态、刷新有效日期并重新复制连接；不要通过提高日志详细度记录完整 `/listen` 地址。`503` 表示凭据已通过校验，但频道已停用、歌单不可用、FFmpeg 无法启动或连接容量暂时不足，应检查频道健康状态与应用日志。

### 上传返回 413

确认请求是否走最终 Nginx 配置。原始音乐内容接口允许 500 MiB，其他 API 仍限制为 1 MiB；预约时声明大小也不得超过 500 MiB。

### 上传一直排队或返回 storage_unavailable

检查公共队列是否已有 10 个未结束任务、页面心跳是否正常，以及 `storage.locations` 中是否至少有一个已挂载、服务账号可写且预计占用率未超过上限的位置。检查额外挂载是否同时出现在 systemd 的 `RequiresMountsFor` 和 `ReadWritePaths` 中。

### SQLite database is locked

确认只运行一个 Uvicorn worker 和一个 `radio.service` 实例，并检查数据库是否位于本地磁盘而不是网络文件系统。

### 跨境访问不稳定

优先检查客户端到服务器的路由、云防火墙和运营商网络。HLS 使用多个短请求，持续丢包会导致缓冲。必要时使用更合适的地域或合规 CDN，但 CDN 接入必须继续保护私有 HLS。
