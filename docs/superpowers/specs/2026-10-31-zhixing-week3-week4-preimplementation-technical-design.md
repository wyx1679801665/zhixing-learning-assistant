# 知行平台第三、第四周编码前分析与技术设计

日期：2026-10-31 ｜ 版本：1.0 ｜ 状态：编码依据，尚未实现验证

## 1. 文档定位和设计输入

本文件完成生成业务源码之前的需求分析、系统设计、数据与接口设计，并给出源码组织方案。它不是逐日排期表，也不是实现完成报告。先按本文设计业务边界与契约，再生成源码、联调和验证。

课程依据：根目录《卓越软件工程_第2至8周项目推进与每周汇报说明.docx》，正文《卓越软件工程项目实践精炼版》。第三周强调架构、统一数据、模块骨架和首条真实流程；第四周强调集成版本、模块状态和缺口。

产品与技术依据：现有 requirements-spec v3.2、technical-design v3.2，以及模块深讲设计。界面与交互基线明确采用 `docs/superpowers/specs/zhixing-prototype_1.1.html`，不回退到旧版原型。本文细化两周实现设计，不删除最终必做范围。既有技术设计的第20节及补充契约优先于早期示例。

现有 HTML 是可操作的前端成品原型，应保留其导航、视觉、表单和业务顺序；它尚不能证明服务器认证、共享存储、真实评分或模型能力已实现。未发现独立前后端实现目录，源码设计按新建模块化工程处理。

## 2. 原型分析与真实系统差距

| 已有原型 | 当前行为 | 真实系统设计 |
| --- | --- | --- |
| login、switchRole | 无密码切换演示身份 | 账号密码认证，服务器会话；登录角色决定导航 |
| home、records | 从浏览器共享 db 统计 | 查询当前学生的学习事件与进度聚合 |
| goal、path、replan | 示例诊断、路径与重规划 | 后端保存目标、作答、路径版本；生成运行可追踪 |
| resources、publish、kbdetail | 浏览器文件和元数据；支持阅读、播放、下载 | 服务器文件存储、班级授权、发布状态、解析任务和资源版本 |
| search | 文本关键词匹配，预置推荐，打开搜索引擎 | 授权范围内全文/向量检索；真实搜索与预置示例分开标识 |
| tasks、quiz、assign | 示例题目评分、提交与撤回 | 持久任务分配、作答、确定性评分、反馈和幂等 |
| classes、students、progress | 示例班级与学生统计 | 教师所有权、成员关系、班级统计与授权画像 |
| accounts、settings | 浏览器账号状态、模型配置示意 | 管理员服务端账号管理、秘密配置与配置版本 |

原型已经增加课程知识库：文件上传、标签说明、音视频播放、字幕关联、学习时长、网址登记。正式版保留这些能力的入口和语义；文件保存与正文可检索分别建模。音视频存在不等于有转写，图片存在不等于有 OCR，网址登记不等于已抓取或验证。

原型中的角色共用 db、资源缺少真实班级归属、固定学生姓名、示例路径和评分均需替换。localStorage 不再存储权威业务数据；可保留非敏感界面偏好。IndexedDB 文件不自动迁移为正式课程资源，初始化使用明确的演示数据脚本。

## 3. 业务范围与用例

角色约束：管理员维护账号和系统配置；教师只管理本人班级、资源和任务；学生只访问本人记录及已加入班级的已发布资料。管理员角色不默认取得所有学生私密学习内容。

基础教学链：管理员建号 → 教师建班并生成邀请码 → 学生入班 → 教师发布资源与任务 → 学生学习和作答 → 系统评分反馈 → 学习事实入库 → 学生查进度、教师查本班情况。

智能学习链：学生创建目标与预算 → 形成候选知识结构 → 检查资源、题目及来源覆盖 → 必要时联网补齐 → 诊断与掌握度初始化 → 生成并校验路径 → 学生接受 → 学习测验 → 掌握度更新 → 重规划预览 → 接受或拒绝。

| 阶段 | 设计落地范围 | 验收重点 |
| --- | --- | --- |
| 第三周 | 认证、班级、资源、任务、客观反馈、学习记录和基础统计；真实模型连通试验 | 首条真实链、权限隔离、持久化 |
| 第四周 | 基础功能完善；单主题目标诊断、路径、打卡、BKT、重规划、画像和四 Agent 初次集成 | 同一版本跨模块闭环、规则与状态正确 |
| 后续补齐 | 完整主观复核、复杂资源解析、多主题与联网异常、模型评估激活等剩余必做项 | 按原需求第五周全部接入，不能隐性删减 |

## 4. 架构设计与技术决策

采用 React + TypeScript + Vite、FastAPI、SQLAlchemy/Alembic、SQLite/FTS5、Chroma、LangGraph 和服务端千问网关，延续既有技术基线。这里不指定未经环境验证的依赖版本；编码启动时验证 Windows 安装和真实能力并锁定依赖。

浏览器通过同源 `/api/v1` 访问后端，开发期由前端代理转发。后端按认证、班级、资源、任务、评测、规划、画像拆分模块，统一进程部署。SQLite 保存业务事实；文件目录保存附件；FTS5/Chroma 是可重建检索索引，不是业务真相来源。

请求依次经过身份校验、输入 Schema、对象授权、业务服务、事务和返回 DTO。接口层不直接调用模型或自行聚合画像；Agent 调用服务，不绕过权限和事务直接写表。

生成和解析采用持久运行记录，POST 返回202及 run_id，GET只查询。单机后台执行器处理任务；运行恢复以持久状态与幂等事件为依据，不能依赖进程内列表。第三周优先落实教学链，第四周接入完整运行轨迹与检查点。

文件上传不在长数据库事务中执行。先保存临时文件、校验并创建资源版本，解析和索引完成后原子切换 SQL 索引指针。Chroma 与 SQLite 不构成跨库事务；失败保留明确状态，允许幂等重建。

## 5. 模块职责和依赖

| 模块 | 拥有的数据与规则 | 对外提供 | 不承担 |
| --- | --- | --- | --- |
| auth/accounts | 密码、会话、账号状态、角色 | 当前身份、建号、停用、重置 | 班级事实聚合 |
| classrooms | 班级、成员、邀请码及所有权 | 班级授权、成员查询 | 学生私密目标访问授权 |
| resources/retrieval | 文件版本、解析文本、索引、检索来源 | 授权阅读、学习记录入口、检索证据 | 将上传成功等同于解析成功 |
| teacher_tasks | 任务、分配快照、截止与撤回 | 学生待办、教师进度、任务负荷 | 直接改写 AI 路径 |
| assessment/mastery | 作答、评分、证据、BKT | 反馈、掌握度版本 | 从阅读时长推断掌握 |
| goals/planning | 目标、诊断关联、图谱、路径版本 | 候选生成、规则审查、首次规划 | 画像数值生成 |
| budget/replan | 全局预算、预约、预览与版本检查 | 预算查询、接受/拒绝 | 覆盖其他目标预约 |
| profiles/progress | 事实聚合、快照、水位和数据质量 | 本人画像、本班画像、历史 | 修改原始成绩和学习事实 |
| agents/llm | 四 Agent 路由、运行轨迹、模型适配 | 结构化候选和解释 | 最终授权、评分与路径可行性裁决 |

依赖顺序：auth → classrooms → resources/teacher_tasks → assessment/events → progress/profiles；planning 消费授权证据、掌握度和预算，replan 调用 planning。提前定义预算 DTO，避免规划与重规划循环依赖。

## 6. 数据模型设计

通用约定：稳定 UUID 主键，时间统一保存 UTC、按学生时区展示；版本冲突使用整数 version。删除优先采用业务状态，不级联删除历史作答。以下为逻辑表设计，物理字段在迁移中实现。

| 实体 | 关键字段与约束 |
| --- | --- |
| users/sessions | username唯一、password_hash、role、status、auth_version；会话token_hash唯一、expires_at、revoked_at、签发时auth_version |
| classrooms/memberships/invites | owner_teacher_id、status、version；成员(class_id,user_id)唯一；邀请码仅存hash，含有效期、使用上限、计数、撤销状态 |
| resources/resource_versions | owner、class_id、scope、title、kind、tags、description、publish_status；版本含file_path、hash、bytes、parse_status、index_status |
| resource_transcripts/chunks | 字幕关联资源版本、格式、文本及版本；chunk含resource_version_id、序号、文本、页码/时间段、来源定位 |
| teacher_tasks/task_items/assignments | class_id、创建教师、状态、截止、预计分钟；题目与答案版本不可变；分配(task_id,student_id)唯一 |
| attempts/answers/score_reviews | student_id、assignment_id或diagnosis_id、attempt_no、状态；答案关联题目版本；复核保留旧分数和原因 |
| learning_events | student_id、class_id可空、source_type/id、minutes、occurred_at、event_key唯一；不同活动避免重复计时 |
| goals/diagnoses | owner、target_skill_ids、预算、状态、version；诊断绑定目标与题目快照 |
| knowledge_nodes/edges/evidence | 图谱快照、先修边；证据关联题目/资源版本/URL快照；draft与verified分开 |
| mastery_observations/snapshots | student、skill、evidence_id、结果、参数版本；同一有效证据仅应用一次；快照含掌握概率与版本 |
| budgets/reservations | 学生日周上限、时区、version；预约来源、日期、分钟、状态；涵盖所有活动目标和教师任务 |
| paths/path_tasks/replan_previews | goal、图谱/掌握度/预算版本、状态、校验结果；预览绑定旧活动路径和所有输入版本 |
| profile_snapshots/refresh_events | student或class作用域、version、window、watermark、payload、data_quality；source_event_id去重 |
| agent_runs/agent_steps/audit_logs | actor、运行状态、阶段、错误、检查点、输入版本；审计含操作人、对象、动作、时间和安全摘要 |

主要关系：教师1:N班级；学生经membership关联多个班级；班级1:N资源和任务；任务1:N分配，分配1:N作答；目标1:N路径但最多一条活动路径；证据驱动掌握度和画像事件。

阅读记录采用学习会话标识；同一保存请求重试不新增分钟，用户修改同一会话时替换原值并记录变更。不要使用原型中单个全局minutes字段作为数据模型。

## 7. 状态、事务和核心算法

资源分别维护发布状态draft/published/withdrawn、解析状态pending/processing/ready/failed和索引状态；附件可播放或下载不代表正文可参与RAG。下架后停止新的学生读取和检索，保留已有作答所需来源快照。

任务draft → published → withdrawn/closed。发布时冻结题目与分配名单；晚入班学生不自动获得旧分配。作答submitted → scored或pending_review → scored。主观题待复核不更新BKT。

路径pending_acceptance → active → superseded，候选可rejected；内容draft/verified独立于路径状态。接受必须重新比较目标、掌握度、预算、预约版本；不一致返回409。

评分事务：校验分配与题目版本 → 保存答案 → 客观规则评分 → 写证据 → 更新BKT → 写学习事件与待刷新事件 → 提交。相同作答ID重复请求返回原结果，即使换幂等键也不重复增加掌握度。模型解释失败不回滚成绩。

BKT使用既有参数契约。先按正确/错误观察计算后验，再计算学习转移：P(next)=P(posterior)+(1-P(posterior))*P(T)。参数P(L0)、P(T)、P(G)、P(S)版本化；评分更正重放观察序列，不能追加一次增益。

规划：授权资源检索 → 候选知识图与题目覆盖检查 → 缺口补齐 → 诊断 → 按先修拓扑排序 → 结合掌握度筛选 → 在全局剩余预算中排期 → 校验来源、先修、重复、负荷 → 保存待接受路径。图有环、来源不足、预算不足分别给出明确错误或草稿状态。

重规划：基于当前路径和输入版本创建预览；拒绝只记录选择；接受在短事务内重新验证版本、替换本目标预约并切换活动路径。画像消费事件生成不可变快照，同水位不重复生成，刷新失败保留旧快照并标stale。

## 8. API契约设计

所有路径前缀 `/api/v1`。响应为DTO而非数据库对象；列表分页。错误统一包含code、message、request_id和可选details，区分401、403/防泄露404、409、422、503。密码、答案密钥、存储物理路径和模型密钥不进入普通响应。

| 接口族 | 操作 | 关键约束 |
| --- | --- | --- |
| /auth/login、/logout、/me | POST、POST、GET | HttpOnly会话Cookie；写入校验Origin/CSRF；登录限速 |
| /admin/users | POST、GET；/{id}/disable、/reset-password POST | 仅管理员；状态或auth_version变化立即使旧会话失效 |
| /classes、/classes/{id}/invites | 建班、查询、创建/撤销邀请码 | 教师所有权；邀请码明文仅创建时返回 |
| /class-invites/{code}/join | POST | 学生身份；事务控制使用次数；重复加入幂等 |
| /resources | multipart POST、GET | 文件、class_id、标签说明；服务端校验类型大小和归属 |
| /resources/{id}/publish、/withdraw | POST | 检查资源状态和教师所有权 |
| /resources/{id}/content、/download、/transcripts | GET、GET、POST | 每次鉴权；字幕绑定资源版本 |
| /retrieval/search、/web-search-runs | POST | 先按授权过滤候选再检索；联网是显式生成操作 |
| /teacher/tasks、/learners/me/tasks | 发布/列表 | 分配快照，不返回答案 |
| /tasks/{id}/attempts | POST | 答案列表、client_submission_id、题目版本；返回attempt_id、评分状态及反馈 |
| /learners/me/study-sessions | POST/PATCH | resource_version_id、分钟、会话标识；正数范围校验和去重 |
| /goals、/goals/{id}/diagnoses | 创建/修改、创建诊断 | 当前学生所有权；版本校验 |
| /goals/{id}/planning-runs、/runs/{id} | POST、GET | POST返回202/run_id；GET返回状态、阶段、错误或结果引用 |
| /paths/{id}/accept、/check-ins | POST | 输入版本、幂等标识；打卡与测验分开 |
| /goals/{id}/replan-previews | POST | 返回预览及输入版本；/{id}/accept或/reject执行选择 |
| /learners/me/progress、/profile、/profile/history | GET | 本人事实和画像 |
| /teacher/classes/{id}/profile及学生投影 | GET | 本班授权；投影排除班外私密信息 |

GoalSpec采用target_skill_ids数组；路径DTO含path_id、version、content_status、status、validation、tasks、sources及输入版本；任务含skill_id、先修、预计分钟和日期。检索结果含resource_version_id、chunk_id、定位与分数，不把分数当作覆盖充分的结论。

## 9. 前端设计与原型迁移

保留原型绿色视觉、侧边导航、卡片和三端工作台。将内联onclick及字符串模板改为React组件和表单事件，业务行为通过API客户端完成。路由守卫用于导航体验，真正授权仍在服务器。

student对应home/goal/path/resources/read/tasks/quiz/replan/records/search；teacher对应classes/students/publish/assign/progress/search；admin对应accounts/settings。kbdetail改为资源详情路由，以资源ID从后端加载，不依赖临时全局kbDetail。

每个页面定义加载、空数据、失败、提交中、成功和版本冲突状态；写操作等待服务器确认后再显示成功。上传显示逐文件结果，部分失败不抹去成功项。路径生成轮询run状态，离开页面后可恢复查询。

模型配置页首先区分“已保存配置”“能力评估结果”“当前激活版本”，不能保存即激活；第四周未完成的部分明确标记，不用可点击的演示开关冒充服务端能力。

## 10. 源码结构设计（下一阶段据此生成）

建议工程根目录增加frontend、backend、scripts和docs/evidence，不改写归档原型。

| 目录 | 设计职责 |
| --- | --- |
| frontend/src/app | 路由、登录上下文、布局和全局错误处理 |
| frontend/src/features/auth,classrooms,resources,tasks | 与原型基础页面对应的业务组件、查询和表单 |
| frontend/src/features/goals,planning,progress,profiles,admin | 智能链、进度画像与管理页面 |
| frontend/src/shared/api,ui,types | 统一请求、错误映射、公共组件和接口类型 |
| backend/app/api/v1、schemas | 路由输入输出和依赖注入，禁止堆放业务算法 |
| backend/app/core、db、migrations | 配置、会话安全、数据库和迁移 |
| backend/app/services | auth、classrooms、resources、retrieval、teacher_tasks、assessment、mastery、budget、planning、replan、profiles等用例 |
| backend/app/agents、integrations | LangGraph路由、模型/搜索适配，外部响应转换 |
| backend/app/workers | 持久运行调度、解析与画像刷新恢复 |
| backend/tests | 关键算法、权限接口、事务去重和跨模块集成验证 |
| scripts | 初始化演示数据、启动检查、索引重建和备份恢复 |

服务接口边界：AuthorizationService检查对象；ResourceService管理版本；AssessmentService提交并评分；MasteryService应用证据；BudgetService查询/预约；PlanningService生成候选；PathValidator执行规则；ReplanService预览/接受/拒绝；ProfileService生成授权快照。共享Schema从后端OpenAPI同步到前端，避免两人各写一套字段。

源码生成顺序：公共配置与迁移 → 会话/班级 → 资源与文件 → 任务/评分/记录 → 前端基础链 → 目标/检索/规划 → BKT/重规划/画像 → Agent整合。每一层先确定输入输出和失败条件，再写实现；保持可启动的集成版本。

## 11. 安全、失败恢复与设计验证

文件使用服务端随机路径，不接受客户端指定保存路径；限制扩展名、内容类型与大小，不执行上传内容；下载经授权。外链正文抓取必须限制协议、阻止私有/回环目标并校验重定向，避免把登记链接直接交给无约束抓取器。网页和文件内容只作为资料，不能作为系统指令。

密码采用成熟哈希库，会话和邀请码仅存哈希；密钥保存在服务端环境配置或受控秘密存储，不进Git和日志。SQLite短事务，长模型请求不持有写锁；忙碌或超时给出可重试错误。

| 验证场景 | 设计预期 |
| --- | --- |
| 两教师两班交叉读取 | 服务端拒绝；学生端不泄露对象存在性 |
| 提交后重启、重复发送作答 | 结果持久化且不重复评分/BKT |
| 上传成功但解析失败 | 可见失败原因；不伪装可检索正文 |
| 元数据命中但正文缺失 | 不宣称知识覆盖充分 |
| 未达标测验后重规划、拒绝与旧版本接受 | 有掌握度依据；拒绝保留原路径；旧版本409 |
| 模型超时或JSON非法 | 运行明确失败或有限重试，无伪造正式路径 |
| 无学习数据或画像刷新失败 | 显示无数据/旧快照过期，不编造新数值 |
| 账号停用或密码重置 | 旧会话立即失效 |

编码前设计检查：原型页面都有模块映射；实体关系和唯一约束明确；基础链每一步有接口及事务归属；权限检查贯穿资源下载和检索；模型输出不能绕过规则；运行失败与版本冲突有页面反馈。检查完成不等于运行测试通过。

## 12. 阶段产物与后续衔接

本阶段产物为本分析与技术设计文档，以及第10节的源码组织和服务边界设计。下一阶段按设计生成源码，第三周先交付真实教学链，第四周完成智能与画像最小集成；实施结果另记录版本、证据、实际完成状态和缺口。

待编码启动时核实：A/B真实人员、课程周对应日期、模型凭据与能力、单机还是局域网验收、现有源码是否在其他目录。暂按原技术基线的双人、单机多账号环境设计。上传上限、诊断数量和BKT默认参数沿用需求中的暂定值，落入可配置常量并保留决策记录，不将原型限制自动认定为最终需求。
