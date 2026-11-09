# OpenAIR 单机生产部署与跳板暴露

> 状态：已确认
> 更新时间：2026-11-10
> 用途：把 OpenAIR 跑在高性能内网工作站上，通过一台低配公网跳板机以 HTTPS 域名对外提供服务。

本文方案已在以下环境完成端到端验证：

- 工作站：Ubuntu 26.04，Docker 29.1.3，Compose 2.40.3
- 跳板机：Ubuntu 24.04，2 核 CPU、7.4 GB 内存，nginx 已占用 80/443
- 域名：`openair.example.com`，A 记录指向跳板机
- 证书：Let's Encrypt ECC，acme.sh HTTP-01，nginx 终结 TLS
- 隧道：SSH 反向转发，跳板机 `127.0.0.1:18080` 到工作站 `127.0.0.1:8080`

## 1. 架构

```text
浏览器
  -> https://openair.example.com (跳板机 nginx, 443)
  -> 127.0.0.1:18080 (SSH 反向隧道)
  -> 工作站 127.0.0.1:8080 (openair-web)
  -> 容器网络 /api (openair-api)
  -> 模型网关 (工作站出站 HTTPS)
```

跳板机只做 TLS 与流量转发，不需要 Docker，也不运行 OpenAIR 组件。模型调用从工作站直接出站，跳板机不承担推理流量。

## 2. 工作站准备

使用包含必要修复的部署分支。当前 fork 的部署分支：

```bash
git clone https://github.com/ouyangjiahong26/OpenAIR.git
cd OpenAIR
git switch deploy/openair-cislunarspace
```

该分支相对上游 `main` 额外包含：

- MinIO 镜像替换（官方社区镜像已删除）
- planner 重试身份修复（live 模式自动修正不再被误判为重放）
- 思考档位 `reasoning_effort` 支持

配置 `.env` 的关键项：

```dotenv
OPENAIR_CORS_ORIGINS=https://openair.example.com
OPENAIR_GITHUB_OAUTH_REDIRECT_URI=https://openair.example.com/api/auth/oauth/github/callback
OPENAIR_OAUTH_SUCCESS_REDIRECT_URL=https://openair.example.com/auth/oauth/callback
```

四项必填密钥全部使用 `openssl rand` 生成的随机值：`POSTGRES_PASSWORD`、`OPENAIR_MINIO_ROOT_PASSWORD`、`OPENAIR_JWT_SECRET_KEY`、`OPENAIR_WORKER_AUTH_TOKEN`。管理员账号使用强密码。

启动生产端口形态：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

`docker-compose.prod.yml` 会把 PostgreSQL、Redis、MinIO、API 的宿主机端口全部移除，只保留 `127.0.0.1:8080` 给反向隧道。

本机自检：

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/
```

## 3. 工作站反向隧道

创建 `~/.config/systemd/user/openair-tunnel.service`：

```ini
[Unit]
Description=OpenAIR reverse tunnel to jump
After=network-online.target docker.service
Wants=network-online.target

[Service]
ExecStart=/usr/bin/ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=accept-new \
  -R 127.0.0.1:18080:127.0.0.1:8080 \
  jump
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

`jump` 是工作站的 SSH 别名。启动并设置开机自启：

```bash
systemctl --user daemon-reload
systemctl --user enable --now openair-tunnel.service
loginctl show-user "$USER" -p Linger
```

`Linger=yes` 保证工作站重启后用户级服务可以自启。跳板机上确认端口只监听 loopback：

```bash
ss -ltn | grep 18080
# 预期：127.0.0.1:18080
```

## 4. 跳板机 nginx 与证书

域名 A 记录指向跳板机。先放置只含 HTTP challenge 的站点：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name openair.example.com;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
        try_files $uri =404;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}
```

签发并安装证书：

```bash
mkdir -p ~/acme-openair
~/.acme.sh/acme.sh --issue --server letsencrypt \
  -d openair.example.com -w /var/www/html --keylength ec-256
~/.acme.sh/acme.sh --install-cert -d openair.example.com --ecc \
  --fullchain-file ~/acme-openair/fullchain.pem \
  --key-file ~/acme-openair/key.pem \
  --reloadcmd "sudo systemctl reload nginx"
```

完整 HTTPS 站点：

```nginx
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name openair.example.com;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    ssl_certificate     /home/ubuntu/acme-openair/fullchain.pem;
    ssl_certificate_key /home/ubuntu/acme-openair/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 1000m;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;

        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

配置检查与重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 5. 上线验证

从公网机器执行：

```bash
curl -fsS https://openair.example.com/health
curl -fsS https://openair.example.com/health/agent
curl -I https://openair.example.com/
```

预期：

- `/health` 返回 `"status":"ok"`
- `/health/agent` 返回 `skillExecMode=live` 且模型为配置值
- 首页返回 HTTP 200
- `http://` 返回 301 到 HTTPS

登录验证：

```bash
curl -X POST https://openair.example.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"管理员邮箱","password":"管理员密码"}'
```

预期返回 `access_token`。

## 6. 运维清单

- 工作站关闭睡眠与休眠，Docker 服务设为自启。
- `.env` 权限 `chmod 600`，不提交仓库。
- 跳板机防火墙只放行 22、80、443；18080 只绑定 loopback，不加入防火墙放行。
- 数据卷：`pgdata`、`workspace_data`、`minio_data`、`redis_data`。至少 nightly 备份 PostgreSQL 与 MinIO 数据。
- 证书由 acme.sh 自动续期，续期命令 reload nginx。
- 隧道故障时公网返回 502；先查 `systemctl --user status openair-tunnel.service`，再查工作站 web 容器。
- 更新平台时在工作站拉取部署分支后重新执行 compose 构建；不要在跳板机构建。

## 7. 边界

- 单工作站部署，无高可用；工作站离线即服务不可用。
- 大文件上传与产物下载会占用跳板机与工作站之间的带宽。
- SSH 反向隧道是 TCP 套 TCP；长期弱网环境可迁移到 WireGuard，跳板机 nginx 只需把 `proxy_pass` 改为工作站 VPN 地址。
- 本方案是单域名同源反代，不需要 API 子域名，也不需要在公网暴露 8000、15432、9000、9001、16379。
