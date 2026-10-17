# 项目信息

## vela-tools-service
Vela IoT 平台内部工具服务集合（Flask + Vue3 全栈）。多模块聚合平台，涵盖 Crashlog 分析、自动化测试、CI/CD 流水线、AI 日志分析、运维监控等。
### 仓库信息
- 仓库：ssh://dengwenqi@git.mioffice.cn:29418/vela/vela-tool-service
- 项目部署域名：http://vela.pt.xiaomi.srv/
- 分支： dev
- 提交方式： git push origin HEAD:refs/for/dev
### 组成部分
- frontend 前端网页

## bug_agents
基于 multica fork 的项目， 语言为 GO + Next.js  + PostgreSQL(pgvector)

### 仓库信息
- 仓库：git@git.n.xiaomi.com:vela-engineering-platform/llm/bug_agents.git
- 分支： main
- 提交方式： 生成 gitlab MR

## ai-agent-pipeline
VTF 崩溃/异常的自动根因分析与修复服务。VTF 崩溃/异常的自动根因分析与修复服务。采用 python + claude-agent-sdk

- 仓库：git@git.n.xiaomi.com:vela-engineering-platform/llm/ai-agent-pipeline.git
- 分支： main
- 提交方式：生成 gitlab 

## backtrace_compare
Crash 中 backtrace 相似度比较，采用 python 
- 仓库：git@git.n.xiaomi.com:vela-engineering-platform/llm/backtrace_compare.git
- 分支： main
- 提交方式：生成 gitlab MR
  
## tools-bug-list
存储每日巡检发现的问题，总结成周报，汇总存在到该仓库中
- 仓库：git@git.n.xiaomi.com:vela-engineering-platform/llm/tools-bug-list.git
- 分支： main
- 提交方式： 直接提交 main 分支