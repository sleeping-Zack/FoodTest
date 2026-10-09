# 分类系统在线部署

本目录提供现有 Python 分类系统的 Docker 部署配置。准备好云服务器、域名和外部标准资料后，可运行完整查询、原文页预览及资料下载。**提供这些配置不代表网站已经上线。**

## 当前网站的范围

- 分类规则与本机版一致，保留食品层级、各国标独立分类及原文追溯。
- 在线查询和人工处理记录按浏览器 Cookie 隔离。其他访客不能凭记录 ID 读取、导出、打包或写入该记录。
- 清除 Cookie 或更换浏览器后不能找回原记录；重要记录需要导出。此机制是访客隔离，尚非机构账号或角色授权。
- 标准资料供网站访客查询和下载。部署包不包含报告审查系统、检测报告、内部需求文档或已有查询记录。
- 当前面向小范围分类检索试用；使用 HTTPS 反向代理、受限的后台连接和独立持久化存储。

## 准备资料包

在拥有完整标准资料库的原工作区根目录运行：

```powershell
python -X utf8 food-classifier/deploy/package_library.py --output output/deployment/foodtest-library.tar.gz
```

打包器按明确清单收集：引擎注册的标准原件、三个必要索引、独立分类 JSON 和页级细则缓存；不会递归上传整个工作区。配套清单包含每个文件的大小和 SHA-256。当前资料原件约 3 GB，另需索引及缓存。资料包只传到部署服务器，不提交到 GitHub。

## Linux 云服务器部署

需要已安装 Docker Engine 与 Compose 的服务器、可用域名和标准资料包。可从 2 核、2 GB 内存、20 GB 可用磁盘的小范围试用配置开始，再根据并发和下载情况调整；这是起始配置，并非压测容量承诺。

1. 将域名解析到服务器，允许外部访问 80 和 443 端口。
2. 在服务器获取分类源码，将资料包放到源码根目录。
3. 在源码根目录执行：

```sh
mkdir -p food-classifier/deploy/library
tar -xzf foodtest-library.tar.gz -C food-classifier/deploy/library
python3 food-classifier/deploy/verify_library.py food-classifier/deploy/library
cp food-classifier/deploy/.env.example food-classifier/deploy/.env
```

4. 将 `.env` 中的 `FOODTEST_DOMAIN` 改为实际域名，只填主机名。
5. 启动：

```sh
docker compose --env-file food-classifier/deploy/.env -f food-classifier/deploy/compose.yaml up -d --build
docker compose --env-file food-classifier/deploy/.env -f food-classifier/deploy/compose.yaml ps
```

Caddy 为已正确解析的域名申请 HTTPS 证书。只有网关暴露外部端口，Python 服务在容器网络内运行。标准库只读挂载；查询数据库和预览缓存保存在 `query_data` 卷中，证书位于独立卷。日常停止使用 `docker compose ... down`，不要加 `-v` 删除持久化数据。

## 其他支持 Python 或 Docker 的云平台

### 已有 Nginx 网站时使用独立路径

`compose.existing-nginx.yaml` 只启动分类后台，并映射到本机 `127.0.0.1:18011`。配置 `PUBLIC_ORIGIN` 为现有网站的 HTTPS 根地址；应用路径固定为 `/foodtest`。将 `nginx-subpath.conf` 加入该域名现有的 HTTPS `server` 块，先备份配置并运行 `nginx -t`，通过后再平滑重载。不要用默认双服务配置占用已有网站的 80/443 端口。

页面资源、查询接口、原文、下载和导出均支持该前缀。浏览器使用 `https://实际域名/foodtest/` 访问；原网站首页及其他路径继续由原服务处理。

服务器连接 PyPI 较慢时，可先在网络通畅的电脑下载 Linux x86_64 / Python 3.12 的安装包，再传到 `food-classifier/deploy/wheels/`。本轮验证版本为 `pypdfium2==5.13.0` 和 `Pillow==12.3.0`。构建时同时指定 `-f food-classifier/deploy/compose.offline.yaml`，使用离线安装文件；安装包目录已忽略，不提交到源码仓库。

### 独立托管平台

可以使用同一 Dockerfile，配置实际 HTTPS 根地址 `PUBLIC_ORIGIN`、平台端口 `PORT`、监听地址 `FOOD_CLASSIFIER_BIND=0.0.0.0` 和持久化数据路径 `FOOD_CLASSIFIER_DATA_DIR`。将 `references/` 及 `guide-pages.json` 放到镜像中对应的挂载位置。平台需支持约 3 GB 原文资料和持久化存储；只有源码仓库并不足以启动。

健康检查为 `GET /api/health`。平台网关须保留外部 `Host`；应用只接受已配置的网站地址和同源访问，内部 localhost 仅开放健康检查。不要对公网直接暴露未受 HTTPS 网关保护的后台端口。

GitHub Pages 仅运行静态网站，不能直接运行本系统的 Python 后端，且发布站点不超过 1 GB。GitHub Codespaces 是会因空闲停止的开发环境，可用于临时演示。资料来源：[GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)、[Pages 限制](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)、[Codespaces 生命周期](https://docs.github.com/en/codespaces/about-codespaces/deep-dive)、[Caddy HTTPS 反向代理](https://caddyserver.com/docs/quick-starts/reverse-proxy)。

## 部署后检查

- 两个独立浏览器分别搜索“年糕”和“苹果”，确认各自历史中只有自己的记录。
- 年糕应展开米粉制品的 8 条记录，原文页及国标下载可用。
- 重启服务后，同一浏览器仍可查看此前记录。
- 为 `query_data` 卷安排服务器侧备份；分类资料快照按清单保留。

本地回归和共享模式检查：

```powershell
python -X utf8 -m unittest discover -s food-classifier/tests -p "test_*.py" -v
```

测试共享模式的 HTTP 行为不等于完成云端 HTTPS、容器和网络验收。没有可用的云端部署目标时，不能宣称完成上线。
