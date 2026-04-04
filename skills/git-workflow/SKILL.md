---
name: git-workflow
description: Git工作流技能 - 提交规范、分支策略、冲突解决、提交验证。当你涉及git commit、git push、分支操作、合并、提交信息编写、代码提交时必须使用此技能。即使用户只是说"提交一下"或"git"，也应触发。
---

# Git工作流技能 (Git Workflow Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[Git核心清单]** ① commit前必须`git diff --staged`检查暂存内容，❌盲目`git add .` ② message格式`<type>(<scope>): <description>`，type=feat/fix/refactor/test/docs/chore ③ 一个逻辑变更=一个commit，禁止把不相关改动混在一起
> **[提交三禁]** ❌未检查暂存内容就commit（可能提交调试代码/敏感信息/无关文件） ❌force push到共享分支（覆盖他人工作无法恢复） ❌commit message写"fix"/"update"等无意义描述（无法追溯变更原因）
> **[验证铁律]** push前必须确认：分支正确+编译通过+暂存内容正确。commit后用`git log -1 --stat`验证提交内容

---

以下为完整Git工作流审查流程，当用户请求`/git-review`或需要深度Git操作时加载：

## 完整审查流程

### Phase 1: 提交前检查

1. 暂存内容审查：
   - 执行`git diff --staged`查看所有将要提交的变更
   - 确认无调试代码（console.log/print/debugger/TODO临时注释）
   - 确认无敏感信息（密钥/密码/token/内网地址）
   - 确认无无关文件（.DS_Store/node_modules/build产物/IDE配置）

2. 文件范围检查：
   - 确认所有变更文件都属于本次逻辑变更
   - 如果有不相关改动，拆分为多次commit
   - 新文件确认已被git track（`git status`检查Untracked）

### Phase 2: Commit Message规范

3. Message格式（Conventional Commits）：
   ```
   <type>(<scope>): <description>
   
   [可选] <body>
   
   [可选] <footer>
   ```

4. Type定义：
   | type | 用途 | 示例 |
   |------|------|------|
   | feat | 新功能 | feat(auth): 添加OAuth2登录 |
   | fix | Bug修复 | fix(parser): 修复空输入崩溃 |
   | refactor | 重构（不改功能不修bug） | refactor(utils): 提取公共日期格式化 |
   | test | 测试相关 | test(auth): 添加登录失败测试用例 |
   | docs | 文档 | docs(readme): 更新安装步骤 |
   | chore | 构建/工具/依赖 | chore(deps): 升级Swift Crypto到3.0 |
   | style | 格式调整（不改逻辑） | style: 统一缩进为4空格 |
   | perf | 性能优化 | perf(list): 大列表改用虚拟滚动 |

5. Description规则：
   - 用祈使句（"添加"而非"添加了"）
   - 首字母小写（英文时），不超过50字符
   - 说**做了什么**，不说"修改了xxx文件"
   - 有关联Issue时在footer加`Closes #123`

### Phase 3: 分支策略

6. 分支命名：
   - 功能分支：`feature/<描述>` 或 `feat/<描述>`
   - 修复分支：`fix/<描述>` 或 `hotfix/<描述>`
   - 实验分支：`experiment/<描述>`
   - 发布分支：`release/<版本号>`

7. 分支操作安全规则：
   - push前确认当前分支：`git branch --show-current`
   - merge前确认目标分支是最新的：`git fetch origin && git log --oneline HEAD..origin/main -5`
   - ❌ 禁止force push到main/master/develop等共享分支（覆盖他人提交，无法恢复）
   - ❌ 禁止直接在main上开发（绕过Code Review）
   - 合并冲突必须逐文件解决，❌禁止`git checkout --theirs .`全盘接受（全盘接受=丢弃自己所有改动，可能静默丢失重要代码且无法察觉）

### Phase 4: 提交验证

8. 提交后验证（每次commit后必须执行）：
   - `git log -1 --stat`：确认提交内容和message正确
   - `git diff HEAD~1 --name-only`：确认变更文件列表符合预期
   - 如果提交有误：`git commit --amend`修正（仅限未push）

9. Push前验证：
   - `git branch --show-current`：确认分支正确
   - `git log origin/<branch>..HEAD --oneline`：查看将要push的commit列表
   - 确认编译/测试通过（至少能编译）
   - push后检查：`git log --oneline -3`确认远程已同步

### Phase 5: 常见问题处理

10. 提交失败处理：
    - `git commit`返回错误 → 检查pre-commit hook输出
    - `git push`被拒绝 → 先`git pull --rebase`再push
    - push后发现问题 → `git revert HEAD`创建反向提交（❌禁止force push）

11. 工作区管理：
    - 临时切换分支前：`git stash push -m "描述"`
    - 切换回来后：`git stash pop`
    - 查看stash列表：`git stash list`

### Phase 6: 输出报告

```
## Git操作报告

### 提交摘要
| Commit | Message | 变更文件数 | 插入 | 删除 |

### 分支状态
- 当前分支：
- 与远程差异：ahead/behind

### 检查结果
| 检查项 | 状态 | 备注 |
| 暂存内容无调试代码 | ✅/❌ | |
| 暂存内容无敏感信息 | ✅/❌ | |
| Message格式正确 | ✅/❌ | |
| 编译通过 | ✅/❌ | |
| 分支正确 | ✅/❌ | |
```

## Git操作强制规则

### 执行git命令前必须确认
1. 当前所在分支（`git branch --show-current`）
2. 工作区状态（`git status`）
3. 暂存区内容（`git diff --staged`，如果要commit的话）

### 强制规则
- ❌ 禁止`git add .`后不检查暂存内容（可能提交.env/密钥/build产物/调试代码）
- ❌ 禁止commit message写"fix"/"update"/"change"等无意义描述（三个月后无人能看懂这次改了什么）
- ❌ 禁止force push到共享分支（覆盖他人提交，且无法恢复）
- ❌ 禁止跳过编译/测试直接push（破坏CI/CD流水线，阻塞其他人）
- ❌ 禁止一个commit包含不相关的多个改动（无法单独revert/cherry-pick，回滚时牵连无辜代码）
- ✅ 每次commit后必须`git log -1 --stat`验证
- ✅ push前必须确认分支正确+检查将要push的commit列表
- ✅ 合并冲突必须逐文件理解后解决，不盲目accept

## 高级Git操作

### 交互式Rebase（整理提交历史）
```bash
# 整理最近N个commit（合并/重排/修改message）
git rebase -i HEAD~3

# 交互界面中的操作：
# pick   = 保留该commit
# squash = 合并到上一个commit（保留message）
# fixup  = 合并到上一个commit（丢弃message）
# reword = 修改commit message
# drop   = 删除该commit
# edit   = 暂停让你修改该commit的内容

# ⚠️ 只对未push的本地commit做rebase，已push的禁止rebase
```

### Cherry-pick（选择性合并）
```bash
# 从其他分支摘取特定commit到当前分支
git cherry-pick <commit-hash>

# 摘取多个commit
git cherry-pick <hash1> <hash2> <hash3>

# 摘取一个范围（不含起始commit）
git cherry-pick <start-hash>..<end-hash>

# 只应用变更不自动commit（需要手动修改后再commit）
git cherry-pick --no-commit <hash>

# 冲突时
git cherry-pick --continue   # 解决冲突后继续
git cherry-pick --abort      # 放弃
```

### Git Bisect（二分查找Bug引入点）
```bash
# 启动bisect
git bisect start
git bisect bad                    # 标记当前版本有bug
git bisect good <known-good-hash> # 标记已知正常的版本

# Git自动checkout中间版本，你测试后标记：
git bisect good   # 该版本正常
git bisect bad    # 该版本有bug
# 重复直到找到引入bug的commit

# 自动bisect（用脚本判断）
git bisect run go test ./...      # 测试通过=good，失败=bad

# 结束bisect
git bisect reset
```

### Pre-commit Hooks
```bash
# .git/hooks/pre-commit（文件需要chmod +x）
#!/usr/bin/env bash
set -euo pipefail

# 检查是否有敏感信息
if git diff --cached | grep -iE '(password|secret|api_key|token)\s*[:=]' | grep -v 'test'; then
    echo "❌ 检测到可能的敏感信息，请检查暂存内容"
    exit 1
fi

# 检查是否有调试代码
if git diff --cached | grep -E '(console\.log|fmt\.Print|debugger|TODO:.*HACK)'; then
    echo "⚠️ 检测到调试代码，确认是否需要提交"
fi

# Go项目：编译检查
if ls *.go &>/dev/null; then
    go vet ./... || { echo "❌ go vet 失败"; exit 1; }
fi
```

### .gitignore管理
```bash
# 常见需要忽略的文件
.DS_Store
*.log
node_modules/
dist/
build/
.env
.env.local
*.key
*.pem
__pycache__/
.idea/
.vscode/settings.json

# 已被track的文件需要先取消追踪
git rm --cached .env
git rm --cached -r node_modules/
```

### 常用Git命令速查
```bash
# 查看
git log --oneline -20                      # 简洁历史
git log --graph --oneline --all            # 分支图
git log --author="name" --since="2025-01-01"  # 按作者/时间筛选
git blame file.go                          # 逐行追溯修改人
git diff HEAD~3..HEAD -- path/to/file      # 查看指定文件最近3次变更
git show <hash>:path/to/file               # 查看某次commit时的文件内容

# 撤销
git checkout -- file.go                    # 撤销工作区修改（未暂存）
git restore --staged file.go               # 取消暂存（不丢失修改）
git reset --soft HEAD~1                    # 撤销上次commit（保留修改在暂存区）
git reset --hard HEAD~1                    # 彻底撤销上次commit（⚠️丢失修改）
git revert <hash>                          # 创建反向commit（安全撤销已push的commit）

# 清理
git clean -fd                              # 删除未跟踪的文件和目录
git gc --prune=now                         # 垃圾回收
git remote prune origin                    # 清理已删除的远程分支引用
```

## 约束
- 所有git操作必须在用户工作区的正确目录下执行
- commit message必须反映实际变更内容，不得泛泛描述
- 涉及多人协作的分支操作必须格外谨慎
- 不确定操作是否安全时，先`git stash`保存当前工作再操作
