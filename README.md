# 求职助手

一个自己用的求职工作台：把简历、项目材料、岗位 JD 放在一起，帮你改简历、判匹配度、模拟面试、复盘。

**不编造**是它的第一原则：AI 只用你确认过的材料，资料里没有的会明确说「需要你补充」，而不是替你编一段。

---

## 能做什么

| 模块 | 做什么 |
|---|---|
| **材料库** | 上传简历截图 / JD 截图 / 项目文档（含 GitHub 仓库 zip），自动解析成结构化事实，带出处 |
| **岗位** | 录入 JD，四层拆解（硬门槛 / 核心职责 / 加分项 / 隐性偏好）+ 匹配度打分 + 投递建议 |
| **简历** | 对着某个岗位给改写建议（🟢可直接采纳 / 🟡需要补充 / 🔴不建议写），没证据的会被降级；也能和助手对话改 |
| **面试** | 按简历和 JD 出题；**语音模拟面试**（像打电话，会追问，最多追 2 层）；结束后五维评分 + 逐题拆解 + **标准回答案例** |
| **我的** | 项目库、投递看板、变更记录、设置 |

## 技术栈

- 后端：Python 3.13 + FastAPI + SQLite（**一人一个库文件**）
- 前端：原生 ES Module，**无构建步骤**
- AI：DeepSeek（文本）+ 阿里云百炼 DashScope（视觉 / ASR / TTS）
- 语音：流式 TTS，首字节约 0.45 秒

## 本地跑起来

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # 填 DEEPSEEK_API_KEY 和 DASHSCOPE_API_KEY
./run.sh                  # http://127.0.0.1:7870
```

首次启动会自动建一个管理员账号（`.env` 里的 `ADMIN_USERNAME` / `ADMIN_PASSWORD`）。

手机要用麦克风的话得走 HTTPS：

```bash
./run.sh --https          # https://<局域网IP>:8443，自签证书
```

## 跑测试

```bash
.venv/bin/python -m pytest       # 250 条，AI 全部 mock，几秒钟跑完
.venv/bin/python tests/e2e_pipeline.py   # 真实调 AI 的端到端链路
```

## 部署到服务器

见 [`部署文档.md`](部署文档.md)。Docker + Caddy，一条命令起：

```bash
docker compose up -d --build
```

## 目录

```
app/
  main.py         REST 接口 + 鉴权中间件
  auth.py         账号 / 会话签名 / 每日用量配额
  db.py           SQLite，按当前用户选库（一人一个文件）
  clients.py      DeepSeek + DashScope 调用，含流式 TTS
  materials.py    材料解析（截图 / PDF / zip / GitHub 仓库）
  jobs.py         岗位四层拆解与匹配度
  packaging.py    简历改写建议 + 事实库构造
  questions.py    题库生成
  interview.py    模拟面试（追问逻辑、结束闸门、评分、复盘）
  resume_chat.py  简历对话助手
  importer.py     从工作区的 Markdown 档案批量导入
web/              原生 SPA（无构建）
tests/            pytest
```

## 一条设计说明：为什么一人一个数据库文件

数据隔离有两条路：给每张表加 `user_id`，或者一人一个 `.db` 文件。

这个项目选了后者。因为代码里是手写 SQL，前者只要漏一处 `WHERE user_id = ?`
就是数据泄露——别人能看到你的简历和手机号。文件级隔离则是**结构上不可能串**，
而且业务代码一行都不用改（当前用户 id 放在 `ContextVar` 里，`db.session()` 按它选库）。
