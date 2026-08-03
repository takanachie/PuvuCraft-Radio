# PuvuFM Android

`app/` 是 PuvuCraft 旗下 PuvuFM 的原生 Kotlin Android 客户端，仅包含登录、频道选择和直播收听功能。它固定连接现有 PuvuCraft Radio 服务端 `https://www.phi-s.tech`，登录页不允许修改地址。安装后的应用名为 `PuvuFM`，应用 ID 为 `com.puvucraft.puvufm`。

## 兼容范围

- 最低系统：Android 9（API 28）
- 编译与目标 SDK：API 35
- JDK：17
- 构建工具：Gradle 8.9、Android Gradle Plugin 8.7.3
- UI：Jetpack Compose，自适应手机、横屏、平板、折叠屏和分屏窗口
- 播放：Media3 `MediaSessionService`，支持后台、锁屏、耳机和系统媒体控制；AAC/FLAC 均优先使用设备硬件 `MediaCodec`，初始化失败时自动回退到兼容解码器
- 音量：界面百分比通过约 40 dB 的对数曲线转换为播放器增益，并持久化当前音量；取消静音会恢复上次非零音量

布局按当前窗口宽度实时重排，而不是按设备型号判断：

- `< 600dp`：单列手机布局，频道横向切换
- `600–839dp`：宽手机或小平板布局
- `>= 840dp`：频道栏与播放台双栏布局

所有页面均处理安全区域、显示缺口、软键盘、滚动和系统字体缩放。Android Studio 内提供手机登录页与平板收听页 Compose Preview。

应用处于前台且已登录时，每 5 秒静默刷新一次频道状态与当前曲目；进入后台后频道轮询立即暂停，后台播放服务改为每分钟发送一次轻量会话心跳，以持续更新最后活跃时间。回到前台时频道轮询恢复并立刻同步一次。启动图标使用 `res/app.jpg` 提供的 PuvuCraft 图形。

## 使用的服务端接口

客户端只调用已有接口：

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/channels`
- `GET /api/auth/player-key`
- `POST /api/auth/player-key/regenerate`
- `POST /api/auth/player-key/url`
- `GET /listen/aac/{player_key}/{channel_slug}`
- `GET /listen/flac/{player_key}/{channel_slug}`（仅管理员）

登录成功后，客户端通过 CSRF 接口申请播放地址，再把该地址交给后台播放器。默认使用 AAC 320 kbps；服务端返回 `lossless_available` 时，管理员界面会显示 AAC/FLAC 选择器。格式偏好保存在本机并跨登录记忆，普通账号始终回退到 AAC。密码永不落盘；会话 Cookie 使用 Android Keystore 的 AES-GCM 密钥加密后持久化。每次启动时客户端都会删除已过期 Cookie，并通过 `/api/auth/me` 重新校验会话。客户端设置 30 天绝对上限；若服务端 Cookie 或服务端会话更早失效，则以更短期限为准。

播放凭据超过服务端的 30 天新连接窗口后，客户端会先明确提示。确认刷新会使同一账号之前的外部播放器连接失效，这是服务端单凭据设计的既有行为。

## 二级转发 TLS 兼容

正式服务采用二级反向代理，`www.phi-s.tech` 当前出示 `puvu.phi-s.tech` 的证书。客户端只为这一对 HTTPS 主机名提供临时传输映射：

- 页面与会话身份始终固定为 `www.phi-s.tech`，实际网络连接透明映射到证书有效的 `puvu.phi-s.tech`。
- 系统正常验证 `puvu.phi-s.tech` 的主机名、证书链、签名和有效期，不存在接受任意证书的全局 trust-all。
- 若服务端返回以 `www.phi-s.tech` 开头的持续流地址，播放器会将主机改写为证书有效的 `puvu.phi-s.tech`。
- 其他主机、HTTP 和非标准端口不会获得该例外。

服务端完成正确证书部署后，应删除 `TlsCompatibility.kt` 中的临时映射。

## 构建

使用 Android Studio 打开本目录，安装 Android SDK 35，并选择 JDK 17。也可在命令行执行：

```bash
./gradlew test
./gradlew assembleDebug
```

正式版仅允许 HTTPS，以保护密码、会话和播放密钥：

```bash
./gradlew assembleRelease
```

Debug APK 输出到 `app/build/outputs/apk/debug/app-debug.apk`。Release APK 会自动签名并输出到 `app/build/outputs/apk/release/app-release.apk`。

### Release APK 签名

项目按要求将发布密钥和凭据保存在 `signing/` 并纳入 Git，Gradle 会自动使用它们。该密钥属于公开仓库密钥：任何能读取仓库的人都可以签出相同应用身份的 APK，因此不能把签名证书作为发布者真实性或安全边界。后续更新仍须复用该密钥并提高 `versionCode`。

可用以下命令验证构建产物：

```bash
JAVA_HOME=/mnt/b/Android/jdk-17 \
PATH=/mnt/b/Android/jdk-17/bin:/usr/bin:/bin \
/mnt/b/Android/sdk/build-tools/35.0.0/apksigner verify \
  --verbose --print-certs \
  app/build/outputs/apk/release/app-release.apk
```

## 安全边界

- 应用不包含注册、管理、上传或服务端播放控制能力；管理员权限仅用于选择 FLAC 收听流。
- 密码永不保存；会话 Cookie 加密保存，最长 30 天，并在启动或访问时自动清除过期数据。
- 播放密钥不会写入日志、偏好设置或页面文本。
- 媒体服务只接受本应用与系统信任的媒体控制器连接。
- 退出登录会停止当前流、清除媒体项目并删除本地加密 Cookie。
- Android 9 仍受支持；除上述固定二级转发映射外，网络请求继续执行系统 TLS 校验。
