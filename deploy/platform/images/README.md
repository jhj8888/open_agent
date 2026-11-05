# 控制面与工作台镜像交付

`artifacts.json` 固定 API JAR、静态文件、Caddy 2.11.8、JRE 基础镜像和 Helm 4.3.1 摘要。构建只复制经过校验的文件，不在镜像中运行 npm/pip/Maven，不向 build context 放入配置、账号、模型密钥或整个仓库。API 为既有 JAR + 固定 Temurin 基础镜像；web 为 `scratch` + Caddy 静态二进制/工作台，均默认 UID/GID 65531。工作台外观和业务资产与当前展示版一致。

Caddy 和 Helm 官方仓库在 2026-11-05 分别约 78.8K、30.4K stars，均 Apache-2.0。[Caddy](https://github.com/caddyserver/caddy)、[Helm](https://github.com/helm/helm)。Caddy LICENSE 及项目原 LICENSE/NOTICE 随镜像保留；Temurin、Ubuntu、Java 及编译依赖各有许可，当前清单不是完整 SBOM。完整依赖许可/SBOM 审查仍在 OpenSpec 跟踪。

## 离线构建与导入

在可信 Linux amd64 Docker 构建机预先导入 `artifacts.json` 指定的基础镜像，以及已构建的 JAR、工作台目录和 Caddy 二进制。构建工具需要 Python 3.11 或更新版本。构建机必须能访问本地 Docker daemon；工具不自动 pull、不执行在线安装。默认路径要求操作者拥有私有输出目录，可使用普通构建账号，展示机由已授权管理员执行。

将 `Dockerfile.api`、`Dockerfile.web`、`.dockerignore`、`artifacts.json`、`LICENSE-caddy` 以及项目根目录的 `LICENSE`、`NOTICE` 放进一个专用输入目录，随后运行：

```sh
python3 deploy/platform/images/build_images.py \
  --sources /srv/open-agent-input \
  --jar /srv/reviewed/platform.jar \
  --console /srv/reviewed/workbench \
  --caddy /srv/reviewed/caddy \
  --output /srv/open-agent-images
```

缺基础镜像或任一摘要不匹配立即失败。当前已在 Docker 24.0.9 使用本地 legacy builder 验证（`DOCKER_BUILDKIT=0`、`--pull=false`、`--network=none`）；其他 Docker/BuildKit 版本需先验证本地缓存与离线解析行为。输出包含 `build-report.json` 与 Docker save 归档，记录所有输入摘要、构建上下文摘要、镜像 config ID、归档摘要。将归档和报告一起复制到内网，先校验报告中记录的 SHA-256，再 `docker image load --input ...`。

随后按企业仓库已有流程完成登录、tag 和 push。本工具不持有仓库凭据、不替用户发布到公网。**Docker 本地 image/config ID、归档 SHA-256 都不是仓库 manifest digest**，不能直接填进 Helm 的 `images.*.digest`。用私有 registry 返回的真实 digest 运行：

```sh
python3 deploy/platform/images/record_registry_images.py \
  --build-report /srv/open-agent-images/build-report.json \
  --api 'registry.example.internal/open-agent/api@sha256:<实际manifest摘要>' \
  --web 'registry.example.internal/open-agent/web@sha256:<实际manifest摘要>' \
  --output registry-images.json
```

脚本从 Docker 本地 inspect 核对 RepoDigests、预期 image ID 和构建归属，拒绝未导入、错误镜像、裸 config ID、可变 tag 和常见公网仓库；不做隐式网络拉取。企业仓库地址仍需安装人员确认内网可达。输出可直接作为 Helm JSON values 覆盖，不含凭据。实际私有仓库尚未提供，因此本轮没有伪造可拉取的 registry digest。

## 已完成的服务器验证

展示机 `/opt/open-agent/image-build` 保存已导出的约 537 MB 镜像包及构建报告。`verify_images.py` 使用部署好的备份工具，在新目录恢复真实数据副本，创建专用无公网出口网络、独立 MySQL/S3/Casdoor，再启动新 API/web 镜像；使用 Helm 实际渲染的 Caddy 配置。测试覆盖真实工作台登录代理、静态资源/文件摘要、步骤查询、跨租户拒绝、内部路由及伪造头拒绝、任务准入关闭时不写入任务。

展示机 `/opt/open-agent/delivery/current.json` 记录专用 chart 包、镜像工具/许可证、构建/验收报告的版本目录与逐文件摘要；镜像大文件仍存放在上述 `image-build` 目录。交付目录仅授权管理员可读，不通过工作台静态服务发布。此包不包含业务数据、账号或私钥，也尚未包含全平台离线依赖。

演练不替换线上数据，不启动 executor/沙盒，不调用模型。与备份演练相同，暂时停止本项目 MLflow/exporter 释放内存，线上任务观测由 outbox 缓冲；结束恢复服务。所有临时容器/规则有归属标记和持久维护记录。SIGKILL/断电后在同一展示机使用 `verify_images.py --cleanup` 清理；明文恢复目录 root 专用，不自动删除。

这证明已导入制品的控制面容器可在隔离网络启动与读写管理接口，不代表 Kubernetes API/CNI/Kata、集群执行器、完整离线安装、HA、告警通知或容量已经验收。生产依赖与门禁见 [专用 chart](../../../helm/open-agent-platform/README.md)，相关证据见 [交付验证](../../../docs/platform/platform-delivery-verification-20261105.json)。
