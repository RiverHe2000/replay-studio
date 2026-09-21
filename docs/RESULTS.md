# 实际验证结果

记录日期：2026-09-19。所有时间为本机一次观测，不是服务等级承诺。开发主机为 Windows x64、Python 3.12、RTX 4070；完整视频流程以 CPU 模型模式运行。GPU 的小输入测试另行列出。

## 完整流程

| 运行 | 实际范围 | 结果 |
|---|---|---|
| PostgreSQL 16.15＋MinIO，pipeline v2 | HTTP 上传、重放/冲突、真实 ASR/OCR/CLIP、三路检索、20 秒目标剪辑、版本冲突、MP4/SRT/来源导出 | PASS；30 秒样片分析 42.375 秒 |
| SQLite 本机演示，pipeline v2 | 上述流程＋真实 SmolVLM 12 张稀疏帧解释 | PASS；分析 184.891 秒 |
| 全新独立环境，Windows CPU 哈希锁 | 不继承系统包；真实 ASR/OCR/CLIP＋完整 HTTP/FFmpeg 流程 | PASS；分析 37.578 秒 |
| 导出精度 | 保存的目标片段合计 19.580 秒；实际 H.264/AAC 文件 | 19.601333 秒，差约 21 毫秒 |

对应原始报告：[Postgres/S3](../artifacts/verification-pg-s3-v2/verification.json)、[本地完整模型演示](../artifacts/demo-reviewed/verification.json)、[实际导出](../artifacts/demo-reviewed/output.mp4)、[来源清单](../artifacts/demo-reviewed/sources.json)。样片由脚本生成并用操作系统 TTS 配音，每帧标注合成技术演示。模型接收实际视频、音频和采样画面，未向索引注入标准答案。

独立环境的 [完整运行](../artifacts/locked-runtime/verification.json) 与 [依赖/锁哈希](../artifacts/locked-runtime/environment.json) 证明 Windows CPU 安装契约可运行。安装了 hash-locked 依赖，但复用了本机已经下载的公开模型权重，因此不把结果解释为包含冷下载的耗时。

本地 VLM 耗时显著增加，描述有泛化和不完整之处，例如只说屏幕显示软件截图，或只读出章节编号。因此没有把这次执行成功写成事件理解质量提升。更早的失败运行也保留，包括模型依赖缺失、依赖不兼容，以及预算规划把全部候选合并后无法容纳的 [失败记录](../artifacts/demo-final/verification.json)；后者已修复并重跑成功。

## 检索质量的边界

同一个合成样片的 3 道定位题、2 道无答案题，IoU≥0.3：

| 路线 | Recall@1 | Recall@3 | Recall@5 | 无答案正确拒绝 |
|---|---:|---:|---:|---:|
| Speech | 2/3 | 3/3 | 3/3 | 2/2 |
| Speech＋OCR | 1/3 | 2/3 | 3/3 | 2/2 |
| Fusion | 1/3 | 3/3 | 3/3 | 2/2 |

**融合没有超过语音的 Recall@1。** 这个小样本没有统计泛化意义，不能宣传为“准确率达到 100%”。语音已包含大部分答案，而 OCR 候选较密集，会改变排序。完整逐题结果在 Postgres/S3 报告中。

另做了一次显式 screen-only 开发检查：画面里的 `PORT=8080` 没有在旁白中出现。查询 `PORT`、`8080` 和 `PORT 8080` 时，speech 均为空，OCR/fusion 找到 10 秒和 18 秒对应的画面证据。该查询在查看样片后选择，只证明屏幕文字通路确实提供额外信息，不是独立测试集上的性能提升。[检查记录](../artifacts/demo-reviewed/screen-only-probe.json)

## 真实模型小输入检查

[model-smoke.json](../artifacts/model-smoke.json) 记录版本、输入哈希、返回结果和限制：

- faster-whisper base：真实 CPU 和 RTX 4070 CUDA 转写；后续补测了实际 ASR snapshot/revision 加载。
- CLIP ViT-B/32：真实 CPU 查询编码和 CUDA 单帧编码，512 维、有限值、归一化。
- SmolVLM-256M：真实 CPU 单帧及完整演示稀疏多帧调用；不把描述当已核实事实。
- Qwen3-4B：真实 CPU 调用在两个人工编写候选中返回合法证据 ID，严格校验通过。此项不是从视频端到端测剪辑质量。
- Qwen2.5-0.5B/1.5B：分别产生未知 ID/错误 JSON 结构，被拒绝。失败没有伪装成模型规划成功。

CUDA 检查只覆盖小输入，不证明 CUDA 容器、长期稳定性、峰值显存上限或高并发能力。CPU 路径完成不代表 GPU 路径完整部署完成。

## 工程验收

- 在完全独立的虚拟环境中，按哈希锁安装依赖再安装项目；**119 项后端/媒体/工作流测试通过，13.76 秒**。[JUnit](../artifacts/tests/junit.xml)。依赖一致性检查通过，共检查 82 个已安装包；该环境没有继承现有模型开发环境。测试工具发出 2 条上游弃用警告，无失败。
- Ruff、TypeScript 类型检查和前端构建通过；真实浏览器 **3 项通过**，包含版本冲突/dirty 保留、真实上传删除、取消上传释放配额和失败保留续传记录。[浏览器摘要](../artifacts/browser/summary.json)
- 真正终止一个 worker 进程，在租约过期后由新进程恢复真实 FFmpeg 准备任务；旧 token 在新进程运行时被拒绝。[恢复记录](../artifacts/recovery/run-20260919T071336-5f9712/recovery.json)
- PostgreSQL 上 8 路并发抢同一 prepare 任务仅一个 owner；过期 token 被拒；8 路 GPU claim 同时仅一个 owner；独立缓存从 S3 取回原文件并校验哈希。[服务验证](../artifacts/services/verification.json)
- 真实逻辑删除资产经 GC dry-run/apply 清除 S3 对象和本地 cache/work；数据库审计保留。[GC 结果](../artifacts/services/gc-applied.json)
- SQLite 停写备份/恢复比较全部表行数与 21 个媒体对象 SHA-256。[初次恢复记录](../artifacts/backup-drill/verification.json)。备份数据库和原始 snapshot 不进入版本库。
- PostgreSQL＋S3 停写备份后恢复至新的数据库和 bucket，15 张表行数一致、39 个对象哈希一致；恢复后实际登录、HTTP Range 播放、检索、重新渲染和已存在导出下载均成功。同一个曾失败的 Windows 深层缓存路径在修复后重新验证通过。[完整恢复报告](../artifacts/pg-s3-backup/verification.json)

测试覆盖具体不变量：跨项目权限、CSRF、文件路径、上传幂等与取消、租约竞争/失效、GPU 互斥、缓存失效、旧结果提交、空时间线、引用真实性、并发版本、真实变帧率/延迟音轨/采帧时码/编码导出，以及 planner 格式与预算边界。完整复审见 REVIEW.md。

## 尚未验证

Docker Compose 配置可解析，但主机 Docker 引擎启动故障，未成功构建和启动整套容器。Windows 便携 Postgres/MinIO 实测用于覆盖真实服务适配，不能冒充容器验证。

Windows CPU 完整模型锁已经在干净环境安装并跑通三模态流程；Linux/CUDA 模型锁尚未在对应目标环境完整安装运行。没有云端持续运行、真实用户节省时间、独立大数据集准确率、百万任务规模、磁盘满/所有 GPU OOM 场景或多主机长时间故障的实测结论。真实语料和用户研究按 EVALUATION.md 开展后再更新简历指标。
