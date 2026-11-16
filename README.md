# 知行 · 智能学习规划助手

面向学生、教师和管理员的智能学习规划平台。项目沿用团队仓库的目录结构：React/TypeScript/Vite 前端位于 `frontend`，FastAPI/SQLite/Alembic 后端位于 `backend`，运行与验收脚本位于 `scripts`，设计和实现材料位于 `docs`。

## 当前能力

- 账号、班级、课程资料、任务、学习记录和角色权限管理。
- 课程题库、目标诊断、掌握度估计、学习路径生成和重规划。
- 千问兼容接口的云端四智能体规划，包含 60 场景质量评估和人工复核门槛。
- 联网资料检索、教师授权导入、正文复核、发布和可追溯版本引用。
- 本地规划可独立运行；云端模型和搜索需要服务端环境变量中的 API Key。

## Windows 启动

本地统一版可双击 `启动统一版.cmd`。云端模型版可双击 `启动云端版.cmd`；需要联网搜索时使用 `启动云端搜索版.cmd`。启动脚本会在本机提示输入密钥，密钥不会写入仓库。

也可以手动安装并运行：

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements-lock.txt
Set-Location frontend
npm ci
npm run build
Set-Location ..
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

服务默认地址为 <http://127.0.0.1:8000>，停止服务按 `Ctrl+C`。

## 安全与数据

不要提交 API Key、`.env`、`runtime-data`、`backend/data`、数据库、上传附件、初始账号文件、`.venv` 或 `node_modules`。复制 `.env.example` 中的变量名，并在服务进程环境中提供真实密钥。

## 验证

```powershell
$env:PYTHONPATH = 'backend'
.venv/Scripts/python -m pytest backend/tests -q
Set-Location frontend
npm run build
```

主要实现记录和阶段验收说明位于仓库根目录及 `docs/evidence/implementation`。云端模型通过结构校验和自动规则后，教学语义仍需人工审核。
