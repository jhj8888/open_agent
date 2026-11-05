# 集群 Python 运行镜像

当前交付快照包含独立 Kubernetes 沙盒资源核验器与持久绑定协调器，261 项最终镜像检查通过。输入、镜像和约 706 MB 离线归档摘要见 `artifacts.json`；实际 API 与未完成的准入/回收边界见[本轮验证](../../../docs/platform/cluster-sandbox-verification.md)。下文带日期或旧计数的段落保留对应增量记录。

此目录构建受信 executor / supervisor 的 CPU 镜像。模型由已有 HTTP API 提供；租户脚本必须通过独立沙盒执行。镜像默认入口为 `agent.durable.cluster_child`，监督器使用同一镜像并覆盖命令为 `-m agent.durable.cluster_service`。运行时配置由固定 Secret 挂载提供，不进入交付包。

调用关系：当前 `core/agent/uv.lock` + cluster extra → 官方 uv 导出 → PyPA marker/官方 pip 选择 Linux wheel → 包名、版本、文件名和 SHA256 与 uv.lock 逐项核对 → 显式 Git 源码/许可证输入包 → 无网络 Docker 构建 → 非 root 镜像验证。没有修改共享模型、存储、认证或观测接口，也不切换正在运行的展示 worker。

## 准备输入

联网准备机使用 uv 0.10.9、pip 26.3、packaging 25.0。`download_wheels.py` 以已探测基础镜像的完整 marker environment 过滤依赖，再向官方 pip 传递 CPython 3.11、ABI、glibc/manylinux 平台；避免 Windows 准备机误用自身环境。该脚本仅用于联网准备，运行平台不下载依赖。

从仓库根目录导出（命令与当前交付快照一致）：

```sh
uv export --directory core/agent --frozen --extra cluster --no-dev --no-emit-project --format requirements.txt --output-file ../../.local/worker-image-requirements.txt
uv export --directory core/agent --frozen --extra cluster --no-dev --no-emit-project --format cyclonedx1.5 --output-file ../../.local/worker-resolution.cdx.json
python deploy/platform/worker_image/download_wheels.py --requirements .local/worker-image-requirements.txt --metadata deploy/platform/worker_image/base-image.json --destination .local/worker-linux-wheels
python deploy/platform/worker_image/package_inputs.py --wheels .local/worker-linux-wheels --requirements .local/worker-image-requirements.txt --metadata deploy/platform/worker_image/base-image.json --sbom .local/worker-resolution.cdx.json --output .local/worker-image-inputs.tar.gz
```

`base-image.json` 固定官方 Python 3.11.18 slim-bookworm、Linux amd64，记录取得镜像所用镜像站的 manifest digest、实际 config ID、Python/glibc 和 marker 环境。准备其他平台必须重新探测和验证，不能仅改 tag。基础镜像和最终镜像均可用官方 `docker image save/load` 离线转移。`package_inputs.py` 只读取 Git 跟踪的 agent/common Python、锁文件和说明文件，排除测试与隐藏目录，不复制整个 checkout、`.local`、`.env` 或业务配置。

## 离线构建和检查

在已导入基础镜像的 Linux Docker 主机上，使用交付清单中的输入包 SHA256；输出目录必须为新目录或带本工具归属标记的私有目录。

```sh
python build_image.py --archive /private/input.tar.gz --expected <交付清单中的输入包SHA256> --output /private/worker-image-build
python verify_image.py --report /private/worker-image-build/build-report.json --probe ./probe_runtime.py
```

`probe_runtime.py` 是公开测试代码，应以 0644 提供给非 root 检查容器。构建不调用 pull，不运行 apt，不从公网补包，Docker `--network=none`，pip `--no-index --require-hashes --only-binary=:all:`，使用完整原始 requirements 在实际 Linux Python 中安装并执行 `pip check`。准备机选择错误、缺包或 hash 错误会失败。构建限制 1 核/768 MB；输出包含运行镜像和基础镜像归档、SHA256 和本地 image ID。Docker save/load 可能丢失 RepoDigests，因此构建以预加载的精确 config ID 核验，再建立本地内容专属别名；不会用镜像站 tag 回退拉取。

`verify_image.py` 验证归档与导入身份、默认命令、UID/GID 10001、网络关闭、只读文件系统、移除 capabilities、实际模块加载、安装版本、缺配置失败及 OS 包清单。导入检查使用同一 Docker daemon，不能据此宣称空节点冷启动已验收。`verify_contracts.py` 另外挂载显式测试包和预核验 Temporal CLI，运行合成模型/账本/Kubernetes 响应与真实本地 Temporal 的回归；测试包不进入发布镜像。

镜像根目录和应用目录仍需在 Pod 配置中强制只读；本镜像本身不是租户代码隔离边界。集群 Secret 引用、RBAC 和 NetworkPolicy 已在[执行 chart](../../../helm/open-agent-execution/README.md)接线，Kata admission/回收和实际节点/CNI 验收仍待完成，当前不得开启集群总准入。最新镜像含显式仓库凭据绑定、创建拒绝恢复和每次尝试独立的 execd 鉴权，210 项镜像内检查通过；拒绝恢复要求控制面 V1.65 或更新版本；当前摘要见 `artifacts.json`，前一版证据保留供追溯。

## 清单与许可范围

- `requirements.lock`、`input-manifest.json`：本次输入快照及逐文件摘要。源文件摘要是打包时的实际字节；`source_commit` 为基线，未提交的构建工具由构建报告单独记录 SHA256。
- `resolution.cdx.json`：官方 uv CycloneDX 1.5 导出，包含多平台解析图，不能当作最终镜像的精确安装清单。
- `dependency-inventory.json`：155 个实际 Linux wheel 的名称、版本、哈希和许可证文件；镜像检查补充 bootstrap pip/setuptools 和 Debian OS 包清单。
- `supplemental-notices.json` / `licenses/`：对应版本源码补充的 e2b Python SDK、loguru、protobuf-py-ext、tqdm 许可证原文与来源摘要。原 wheel 的 LICENSE/LICENCE/NOTICE 原样保留；Python/Debian 许可保留于官方基础镜像。e2b 包存在于上游依赖图，不代表本平台启用 E2B 云服务。

当前上游项目把 pylint/astroid 等开发工具列为常规依赖，`--no-dev` 不会将其排除；此增量完整保留锁图并明确记录 GPL/LGPL/MPL 等不同许可。不能把整张依赖图笼统标为 Apache/MIT 或宣称已完成企业分发审查。后续需拆分开发依赖、重新验证最小运行集及对应分发义务。新增构建脚本使用 Python 标准库、官方 PyPA packaging 和 pip，没有引入第三方构建服务。

官方参考：[uv 导出](https://docs.astral.sh/uv/concepts/projects/export/)、[pip 目标平台下载](https://pip.pypa.io/en/stable/cli/pip_download/)、[PyPA markers](https://packaging.pypa.io/en/stable/markers.html)、[Docker 构建建议](https://docs.docker.com/build/building/best-practices/)。

2026-11-05 Kata 配置准备与 SDK 镜像验证见 [验证记录](../../../docs/platform/kata-profile-verification-20261105.json)。新增的 CNI 模式仍需独立准入；集群 Skill 入口保持关闭。

V1.66 沙盒资源绑定和监督服务目标校验见 [身份绑定验证](../../../docs/platform/cluster-sandbox-binding-verification-20261105.json)。该镜像需与当前集群 API 同步升级；完整沙盒准入/停止证据服务仍未开放。
