# Skill 平台控制面启动

`com.iflytek.astron.platform.PlatformApplication` 为部署新增 Skill 平台提供独立入口，复用已有租户授权、模型连接、Skill 发布和 runs 服务。入口只扫描这些组件，未引入第二套持久状态或授权规则；原 `HubApplication` 及默认构建保持兼容。

调用链：企业入口 → 用户 JWT / 部署凭据 / 内部服务身份三个明确的安全链 → 平台控制器 → 原有授权和业务服务 → MySQL / S3。独立管理链只将已验证 JWT 的 subject 传给共享租户服务，忽略客户端身份头，成员、账号和环境权限仍由数据库实时检查。旧知识库、语音、工作流和云连接器控制器不在这个入口开放；需要旧完整控制台时继续使用原入口。

## 构建与配置

Java 21、Maven 3.9.9，依赖预先准备到构建环境。官方 Spring Boot 插件负责生成可执行 JAR：

```sh
mvn -B -ntp -f console/backend/pom.xml -pl hub -am \
  -Dplatform.main-class=com.iflytek.astron.platform.PlatformApplication \
  -Dtest=PlatformApplicationTest -Dsurefire.failIfNoSpecifiedTests=false package
java -jar console/backend/hub/target/hub-server.jar
```

默认读取 `platform.yml`，不加载包含旧服务默认地址的 `application.yml`；部署可通过 Spring 的外部配置机制补充配置。所需变量：

| 配置 | 用途 |
| --- | --- |
| `PLATFORM_JDBC_URL`、`PLATFORM_DB_USER`、`PLATFORM_DB_PASSWORD` | 本环境 MySQL 与专属数据库身份 |
| `PLATFORM_OIDC_ISSUER`、`PLATFORM_OIDC_JWKS`、`PLATFORM_OIDC_AUDIENCE` | 已部署的内网身份服务；验证签名、issuer、audience 和有效期 |
| `PLATFORM_S3_ENDPOINT`、`PLATFORM_S3_BUCKET`、`PLATFORM_S3_ACCESS_KEY`、`PLATFORM_S3_SECRET_KEY` | Skill 私有对象存储；沿用官方 MinIO Java SDK 的 S3 协议 |
| `MODEL_CREDENTIAL_KEYS`、`MODEL_CREDENTIAL_ACTIVE_KEY` | 模型凭据加密密钥；格式见模型连接文档 |
| `MODEL_ALLOWED_ORIGINS`、`MODEL_ALLOWED_CIDRS` 等 | 模型目的地址策略，见模型连接文档 |
| `SKILL_RUNTIME_IMAGES` | 已导入且固定摘要的 Skill 基础镜像列表 |
| `PLATFORM_SERVICE_KEYS` | 独立 worker/dispatcher 身份，见 Temporal 接入文档 |
| `PLATFORM_BIND_ADDRESS`、`PLATFORM_PORT` | 默认仅监听 `127.0.0.1:18082`；容器部署显式设置监听地址并限制网络入口 |

这些值由受限配置文件或 Secret 注入，不写入构建产物或浏览器。Flyway 默认验证并应用迁移，禁止 clean 和隐式 baseline。旧库必须完成迁移前备份；新环境需要创建对象桶和身份服务中的用户，并建立本地用户/租户成员记录。本入口不自动把任意已登录用户变成平台管理员。

此入口使用 Spring JDBC，不需要 Redis 或 MyBatis；明确禁用旧依赖传入的 Redisson/Redis 和 MyBatis 自动配置，防止启动期间连接默认 localhost Redis 或创建无关 Mapper。其他共享服务的配置不受影响。独立日志配置写 stdout，默认不打印 HTTP/JDBC 调试信息或异常堆栈；事件、审计仍按各业务服务的权限与保留规则处理。

## 健康与执行边界

`/health/live` 检查进程存活；`/health/ready` 查询新凭据表确认数据库与必需迁移可用，失败返回 503 和 `DOWN`，不返回驱动信息。就绪检查不代表模型、worker 或沙盒可执行。`PLATFORM_RUN_ADMISSION_ENABLED` 仍默认关闭，必须在受信执行服务与资源核验接通后启用。

完整 Spring 启动测试使用明确的数据库/身份测试实现，验证 Bean 接线、管理身份适配及健康错误处理。2026-11-05 已另外完成真实 MySQL、S3、Casdoor 与本入口共同部署，通过真实 PKCE/JWT、租户创建和服务重启验证，见 [部署记录](control-plane-deployment.md)。公网浏览器与真实任务执行链仍在接入。

2026-11-04 本轮构建完成：4 项检查全部通过，包含真实嵌入式 Tomcat HTTP 请求与正常停机；Maven package 成功。核对 JAR 的 Start-Class、平台配置、日志配置及 V1.54 迁移均存在。产物大小 190,919,064 字节，SHA-256 为 `05dda8bf769f25a02bd5edada88a7ead3ea79fde4338f3d1ef765dbd37d3d9fa`。本轮先修正了隐式 Redis/MyBatis 自动配置和一处测试响应路径断言，再完成全部检查；保留初始失败日志。

Context7 当前不可用，已核对 [Spring Boot 3.5 应用配置](https://docs.spring.io/spring-boot/3.5/how-to/application.html)、[官方可执行 JAR 插件](https://docs.spring.io/spring-boot/3.5/maven-plugin/packaging.html)及 [Spring Security JWT 校验](https://docs.spring.io/spring-security/reference/servlet/oauth2/resource-server/jwt.html)，并使用仓库锁定的 Spring Boot 3.5.4 编译验证。
