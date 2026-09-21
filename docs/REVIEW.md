# 独立复审与修复记录

2026-09-19。实现后由不同审阅者交叉检查媒体/检索、API/存储、worker/部署及文档，再针对可复现问题修复、回归。下面记录实际发现，不把“已做审查”当作没有缺陷的保证。

| 问题 | 后果 | 已做的修复与验证 |
|---|---|---|
| 过期任务的状态更新缺少当前租约条件 | PostgreSQL 竞争时可能把已经续租的工作错误标失败 | 行锁＋token/status/expiry 条件更新；租约单测及真实 PG 并发检查 |
| 限制只看最早 100 个候选任务 | 大量未就绪节点挡住后面的可执行任务 | 有界产品范围内检查全部候选；110 个阻塞任务回归 |
| 顶层 visual 成功而嵌套 VLM 失败仍复用 | 安装模型后重新分析仍拿到旧失败 | 嵌套失败不缓存；stage cache 含模型 revision/处理版本 |
| FFmpeg 默认采样 round=near | 把接近后一秒的画面标成时间零 | round=up、真实红/蓝切换视频回归，pipeline 版本升为 2 |
| planner 合并候选没取最早 core_start | 缩短片段后仍引用已裁掉的证据 | 合并保留完整核心范围；clamp 起点与预算回归 |
| 全部候选先合并，超过目标预算后整体丢弃 | 本可选择的短证据也消失，真实 E2E 无法剪辑 | 先选择预算可容纳证据，保留后续核心预算；真实原请求重放与全流程复跑 |
| 合法空模型选择被当作坏输出 | 模型拒答后反而回退生成非空剪辑 | 空列表作为有效 abstention；独立测试 |
| API 可配置 CUDA planner | 绕过独立 worker 的单 GPU 租约 | 同步 planner 限制 CPU；非法设备测试，GPU 任务仍走 worker |
| ASR revision 只记录未用于加载 | 缓存看似变更，实际权重未冻结 | 按 revision 解析下载 snapshot，记录实际 commit；透传/本地目录冲突测试和真实默认 ASR 回归 |
| 客户端可写任意 evidence ID | 来源清单带不存在的引用 | 保存时从指定 committed manifest 枚举合法 ID；手工剪辑可为空，伪造引用被拒 |
| 时间戳评论没绑定素材 | 多素材项目可能跳错录像 | 评论保存 asset_id 并校验所属项目/时长；前端按该素材跳转 |
| 不允许保存空时间线 | 用户删完最后一个片段无法保存 | 允许空保存，仍拒绝空导出 |
| 丢弃续传只清浏览器记录 | 服务端保留 part 文件和配额 | DELETE upload 先事务取消再清文件；晚到写入被拒，UI 失败保留 handle；真实浏览器覆盖 |
| 导出只有代理视频哈希 | 原件、run 与编辑版本来源不完整 | worker 为来源 JSON 补充原件哈希、asset/run/version、时间映射及配置 |
| S3 批量删除没有检查部分失败 | GC 可能错误报告完成 | 检查 DeleteObjects Errors 并报告未完成 |
| 深层 Windows 路径再拼临时后缀 | 实际 S3 恢复验证遇到 260 字符限制 | 下载/复制先使用短 .staging 临时路径再原子移动；中断清理回归及原路径复查 |
| 短暂数据库异常令本地长驻 worker 退出 | 没有容器监管时工作停摆 | 仅 DBAPIError 有界退避重试；once/drain 明确失败，程序错误不吞掉 |
| 已协作退出的取消任务仍占 GPU lease | 其他任务等待不必要的租约到期 | 计算真正结束后 abandon 按旧 token 释放；不能释放新 owner 的资源 |
| Windows 启动脚本使用新 .NET 重载 | 默认 PowerShell 5.1 启动失败 | rooted 判断＋Join-Path＋单参数 GetFullPath |
| Docker/CI 使用已不存在的 npm 锁 | 容器和 CI 构建必失败 | 改用固定 pnpm 与 frozen lock；Docker 实际 build 仍受主机引擎故障限制 |
| 备份产物混在可公开 artifacts | 可能发布账户/session 数据 | snapshot、restored、数据库和凭证文件加入忽略规则；只保留脱敏验证报告 |

安全审阅检查了所有资产/导出入口的项目归属、cookie/CSRF、Origin、路径约束、字幕参数、模型输出 schema、删除后的提交隔离和 immutable export snapshot。真实媒体测试覆盖音视频非零起点、VFR、采帧、无音频、取消和精确重编码。

数据库租约保护“谁可以提交”，不能在网络分区时物理释放卡死进程占用的显存。操作系统级进程监管与合理超时仍有必要。DB 队列暂未做大规模租户公平调度；完整的外部部署安全审计和长期负载测试不在这次实测结论内。

Docker 启动时遇到失效 IPC socket；针对该单一临时文件的清理操作在执行前被自动审批审查拒绝，只返回 `blocked by policy`，未提供更具体原因。没有执行删除，也没有重置或绕过该限制。保留 [主机诊断](../artifacts/docker-startup-diagnostic.json)，改用独立便携服务完成 PostgreSQL/S3 实测。
