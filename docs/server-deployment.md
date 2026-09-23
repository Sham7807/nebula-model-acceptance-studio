# 服务器部署说明

工作台后端设计为只监听环回地址。远程部署通过独立 HTTPS 反向代理访问，不能直接把 Python 服务改成公开、无登录的接口：会话、任务和报告在一个实例中共享。

## 隔离布局

当前部署使用以下布局；在其他服务器操作前先检查名称、目录和端口是否空闲。

| 项目 | 设置 |
| --- | --- |
| 代码目录 | `/opt/xiaoxiao-workbench` |
| 独立 Python 环境 | `/opt/xiaoxiao-workbench/.runtime` |
| 无交互登录的服务用户 | `xxworkbench` |
| 后端 | `127.0.0.1:18878` |
| HTTPS 入口 | `18877` |
| 网关配置 | `/etc/xiaoxiao-workbench/nginx.conf` |
| 报告目录 | `/var/lib/xiaoxiao-workbench/reports` |
| 历史数据库 | `/var/lib/xiaoxiao-workbench/data/workbench.sqlite3` |
| 历史媒体 | 与 SQLite 一起保存（媒体 Blob） |
| 服务 | `xiaoxiao-workbench.service`、`xiaoxiao-gateway.service` |
| 证书检查 | `xiaoxiao-certificate.timer` |

网关是独立 Nginx 进程，使用独立 `-p`、`-c`、PID 和运行目录。不要使用全局 `nginx -s reload` 或重启已有 Nginx；只 reload 本项目的 systemd 单元。已有站点配置不需要修改。

后端以 `xxworkbench` 运行，代码只读，仅报告目录可写。设置 `NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict`、`ProtectHome=true`。后端 CPU 上限为一核，内存软阈值 384 MB、硬上限 768 MB；网关上限为 20% 单核、96 MB。限制用于控制资源争用，不意味着可以承诺任何负载下零影响。

## 安装与代理要求

先运行 `scripts/prepare_kvv.py`，再在专用虚拟环境中安装 `integrations/requirements-api.txt`。新下载的公开 KVV 素材使用 `0644` 权限，允许独立服务用户读取。不要以服务用户运行 Git 更新或安装依赖；不要授予代码目录写权限。

代理必须：

- 在所有页面和 API 上启用 HTTPS 和登录验证，凭据不放入源码或 Git。
- 在改写 Origin 前，仅接受空 Origin 或实际 HTTPS 入口的精确 Origin。
- 转发 `Host: 127.0.0.1:18878` 与 `Origin: http://127.0.0.1:18878`，保留 `X-Workbench-Token`。
- 验证 Basic Auth 后清除发给后端的 `Authorization`。
- 使用根路径部署，前端 API 路径以 `/api/` 开头。
- 登录后页面使用 HttpOnly、SameSite=Strict 会话 Cookie；除 `/api/session` 外的 API 还需要页面令牌。`/api/history/*/media/*` 允许浏览器原生媒体元素只凭会话 Cookie 读取。
- 仅放行外部 HTTPS 端口；18878 保持只监听环回地址。

所有检测入口（含 GPT 专项）的模型列表统一由登录会话保护的 `/api/models` 获取，支持 Bearer、Anthropic、Gemini 和无鉴权模式；渠道 API Key 不写入数据库。页面每次获取列表会刷新页面令牌，失效令牌自动更新一次，登录过期则提供重新登录入口。服务失败时显示原因，不自动切回浏览器直连。完整服务模式下，基础测试、通用检测和 GPT 的跨域请求通过 `/api/proxy` 转发；CCMax / KVV 在服务器运行。单文件直连模式仍需要渠道允许 CORS。

### 模型列表与国外渠道连通性

模型发现会保留渠道的显式版本与前缀，执行有上限的路径兼容、只读重试和分页。诊断区区分 DNS 失败、网络无路由、TLS 校验、超时、限流、鉴权、地区限制及不支持列表，不把失败当作“0 个模型”。跨域或 HTTPS 降级跳转会停止，避免把密钥转发到其他站点。

海外服务的连通性取决于部署服务器的出站网络。管理员可在 `xiaoxiao-workbench` 单元的受保护环境文件中设置 `WORKBENCH_OUTBOUND_PROXY` 为可用的 HTTP(S) 正向代理地址，然后仅重启工作台单元。该变量作用于**模型列表发现和 `/api/proxy` 请求（含流式）**；它不会重配主机全局代理、其他项目或 CCMax/KVV 验收运行器。未设置时使用直连，忽略机器的隐式 `HTTP_PROXY` / `HTTPS_PROXY`。代理账号不能放入 Git；配置值不会返回给浏览器。

发现接口的预算为约 24 秒、最多 12 次 GET、每份响应最多 4 MB、最多 5 页。达到分页上限会明确标记部分列表；短暂错误最多重试一次，`Retry-After` 要求较长等待时直接提示稍后重试。只有服务商支持地区且凭证具备列表权限时，才可能成功获取，代理不会解除上游权限和地区政策。

## IP 证书续期

没有域名时，部署可使用 Let's Encrypt `shortlived` IP 证书。当前安装的 lego v5.5.1 使用 `lego run` 同时处理签发和续期，证书与账户保存在 `/var/lib/xiaoxiao-workbench/acme`，不进 Git。

`xiaoxiao-certificate.timer` 每 6 小时检查一次。TLS-ALPN-01 验证期间需要空闲且公网可达的 TCP 443；证书签发结束后释放 443，网站仍在 18877 提供服务。未来若其他项目使用 443，应先调整证书验证方式。续期成功的部署钩子只校验和 reload 本项目网关。

## 日常维护

服务认证与历史记录由以下环境变量配置：

```ini
WORKBENCH_AUTH_FILE=/etc/xiaoxiao-workbench/auth.json
WORKBENCH_DB=/var/lib/xiaoxiao-workbench/data/workbench.sqlite3
WORKBENCH_REPORTS=/var/lib/xiaoxiao-workbench/reports
```

`auth.json` 只保存 `username` 与 scrypt `password_hash`，不要把明文密码或该文件提交到 Git。数据库启用 SQLite WAL，备份时先停止本项目服务或同时保留 `-wal` / `-shm` 文件。媒体大小限制为单个 16 MiB、全部 32 MiB；远程媒体只保存经过脱敏的链接，不由服务器抓取。

```bash
systemctl status xiaoxiao-workbench xiaoxiao-gateway
systemctl list-timers xiaoxiao-certificate.timer
journalctl -u xiaoxiao-workbench -n 100 --no-pager
journalctl -u xiaoxiao-certificate -n 50 --no-pager
```

只重启本工作台：

```bash
systemctl restart xiaoxiao-workbench
systemctl reload xiaoxiao-gateway
```

更新代码前先确认没有正在执行的验收任务，保留报告目录、登录配置和 ACME 账户。更新后检查：页面与会话可访问、未登录返回 401、错误来源返回 403、后端未公开，以及原有服务仍健康。

在服务器上执行离线回归时使用环回模拟渠道，避免为了部署验证而调用真实计费渠道。最小依赖已验证官方 11 项预检、611 项全套测试的模拟执行和 CCMax 报告下载；没有历史实测报告时对应回归会跳过。

更新时须一并部署 `multimodal-workbench/report-theme.css`，这是服务端和便携报告的共同主题。通用检测 SSE 使用 `/api/proxy` 的 `stream: true` 模式，反向代理应保留 `X-Accel-Buffering: no` 的效果；不要对该响应开启缓冲，否则流式到达时间无法代表上游表现。
