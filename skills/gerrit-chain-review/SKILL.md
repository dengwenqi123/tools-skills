---
name: gerrit-chain-review
description: 通过 Gerrit REST API 给一个 change 及其整条 relation chain（上游依赖 + 下游被依赖）上所有 OPEN 的 patch 批量打分，默认 Vela-AutoTest=+1、Verified=+1、Code-Review=+2，也可只打单个 change 或自定义 label。当用户说"给这条链打分""整条链打 +2""chain review""gerrit 自动打分""帮我把这个 change 打 +2/+1""批量 Verified""一键过 CR"，或贴出 gerrit.pt.mioffice.cn 的 change 链接/change 号并要求打分、投票、过审时使用。不用于代码审查本身（审查用 vela-review / code-review），也不用于回复 reviewer 评论（用 gerrit-reply）。
---

# Gerrit Chain Review

用 `scripts/gerrit_chain_review.py`（只依赖 python3 标准库）通过 Gerrit REST API 打分。打 +2 / Verified 会直接影响共享仓库的合入，所以流程是**先 dry-run 给用户确认，再 --apply**。

## 前置：环境变量

脚本只从环境变量读取凭据，skill 目录里不存任何密钥：

| 变量 | 必填 | 说明 |
|---|---|---|
| `GERRIT_HTTP_USER` | 是 | Gerrit 用户名，如 `miot-robot` |
| `GERRIT_HTTP_PASSWORD` | 是 | Gerrit Settings → HTTP Credentials 生成的密码 |
| `GERRIT_URL` | 否 | 默认 `https://gerrit.pt.mioffice.cn` |

如果脚本退出码为 2（未设置环境变量），把脚本输出的设置说明转述给用户，让用户自己在 `~/.bashrc` / `~/.zshrc` 里 `export` 后重开终端再试。不要让用户把密码发到对话里，也不要替用户把密码写进任何文件——凭据由用户自己管理。

退出码 3 表示认证失败（401）：提示用户检查用户名/密码，或 HTTP 密码是否被重新生成过。

## 工作流

1. **解析输入**：用户可能给 change 号（`10466025`）、`change,patchset`（`10466025,3`）或 change URL，脚本都能识别，原样传入即可。
2. **dry-run**（默认行为，不打分）：

   ```bash
   python3 <skill_dir>/scripts/gerrit_chain_review.py <change>
   ```

3. **把计划展示给用户**：列出将要打分的 change 列表（change,patchset、仓库、分支、标题）和 label，以及被跳过的 MERGED/ABANDONED change，请用户确认。即使用户最初说了"直接打"，也先给出列表——链上可能混入了别人的 patch，用户需要有机会看到。只有用户在本轮明确说"不用确认/直接 apply"时才可以跳过这一步。
4. **用户确认后执行**：

   ```bash
   python3 <skill_dir>/scripts/gerrit_chain_review.py <change> --apply
   ```

5. **汇报结果**：成功/失败数量；失败项附上原因（403 通常是账号没有该 label 权限，409 通常是 change 状态已变化）。

`<skill_dir>` 是本 SKILL.md 所在目录。

## 常用参数

| 需求 | 参数 |
|---|---|
| 只打这一个 change，不追溯链 | `--no-chain` |
| 自定义 label | `--labels "Code-Review=+2"` 或 `--labels "Verified=+1,Code-Review=+1"` |
| 附带评论 | `--message "auto review"` |

## 行为说明

- 链遍历：从起点出发 BFS，对每个 OPEN change 调 `GET /changes/{n}/revisions/current/related` 扩展；遇到 MERGED/ABANDONED 的 change 不再沿它扩展。
- 打分对象是每个 change 的**当前** patchset，三个 label 用一次 `POST .../review` 提交。
- 只覆盖同仓库的 relation chain；跨仓库 topic 不会被带上，需要的话对每个仓库的 change 分别执行。
