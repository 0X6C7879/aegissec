---
name: shell-scripting
description: Shell脚本开发技能 - Bash/Zsh脚本、自动化部署、系统管理、文本处理。当你涉及Shell/Bash/Zsh脚本编写、deploy.sh/build.sh等自动化脚本、cron定时任务、系统管理命令、sed/awk/grep文本处理时必须使用此技能。即使用户只是说"写个脚本"或"改下部署脚本"，也应触发。
---

# Shell脚本开发技能 (Shell Scripting Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[Shell核心清单]** ① 脚本首行`#!/usr/bin/env bash`+`set -euo pipefail` ② 变量必须`"$var"`双引号包裹防止分词 ③ 路径用`"$(cd "$(dirname "$0")" && pwd)"`获取脚本所在目录
> **[安全三禁]** ❌`eval "$user_input"` ❌无引号变量展开 ❌`rm -rf /`或`rm -rf $VAR/`(VAR可能为空)
> **[健壮性铁律]** 命令失败必须有处理（set -e或显式检查$?），临时文件用mktemp+trap清理

写/改Shell脚本时，强制遵守：
1. **Shebang+严格模式**：`#!/usr/bin/env bash` + `set -euo pipefail`（-e:命令失败即退出 -u:未定义变量报错 -o pipefail:管道中任一命令失败即失败）
2. **引号规则**：变量展开必须双引号`"$var"`，防止空格/通配符导致分词。路径变量尤其重要
3. **错误处理**：关键命令后检查返回值，或用`|| { echo "失败"; exit 1; }`
4. **临时文件**：`mktemp`创建 + `trap 'rm -f "$tmpfile"' EXIT`清理，禁止硬编码`/tmp/xxx`
5. **日志输出**：用函数统一日志格式`log() { echo "[$(date '+%H:%M:%S')] $*"; }`，错误输出到stderr
6. **可移植性**：优先POSIX兼容语法，用`command -v`检查依赖是否存在
7. **幂等设计**：脚本重复执行不应产生副作用（mkdir -p / cp而非追加写入）

---

## 脚本模板

### 标准脚本骨架
```bash
#!/usr/bin/env bash
set -euo pipefail

# 脚本所在目录（解析符号链接）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 日志函数
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }
die()  { err "$@"; exit 1; }

# 依赖检查
for cmd in go rsync ssh; do
    command -v "$cmd" >/dev/null 2>&1 || die "缺少依赖: $cmd"
done

# 清理函数
cleanup() {
    # 清理临时文件等
    :
}
trap cleanup EXIT

# 主逻辑
main() {
    log "开始执行..."
    # ...
    log "完成"
}

main "$@"
```

### 部署脚本模板
```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_HOST="user@server"
REMOTE_DIR="/path/to/deploy"
SERVICE_NAME="myservice"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

# 构建
log "构建中..."
cd "$SCRIPT_DIR"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o "build/${SERVICE_NAME}" . || die "构建失败"

# 上传
log "上传到 $REMOTE_HOST..."
rsync -az "build/${SERVICE_NAME}" "$REMOTE_HOST:$REMOTE_DIR/" || die "上传失败"

# 重启
log "重启服务..."
ssh "$REMOTE_HOST" "sudo systemctl restart $SERVICE_NAME" || die "重启失败"

# 验证
log "等待服务启动..."
sleep 2
ssh "$REMOTE_HOST" "systemctl is-active $SERVICE_NAME" || die "服务未正常启动"

log "部署完成 ✓"
```

## 常用模式

### 参数解析
```bash
# 简单参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--port)  PORT="$2"; shift 2 ;;
        -d|--debug) DEBUG=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          die "未知参数: $1" ;;
    esac
done
```

### 条件检查
```bash
# 文件/目录检查
[[ -f "$file" ]]    || die "文件不存在: $file"
[[ -d "$dir" ]]     || mkdir -p "$dir"
[[ -w "$dir" ]]     || die "目录不可写: $dir"
[[ -x "$script" ]]  || chmod +x "$script"

# 字符串检查
[[ -n "$var" ]]     || die "变量为空: var"
[[ "$var" == "yes" ]] && echo "是"

# 数字比较
(( count > 0 ))     || die "count必须大于0"
```

### 远程操作
```bash
# SSH执行远程命令
ssh "$host" "cd $dir && ./deploy.sh" || die "远程执行失败"

# SCP/rsync上传
rsync -az --delete "dist/" "$host:$remote_dir/" || die "同步失败"

# 远程检查服务状态
ssh "$host" "systemctl is-active $service" && log "服务运行中" || die "服务未运行"
```

### 文本处理
```bash
# grep提取
grep -oP '(?<=version=)[0-9.]+' config.txt

# sed替换（原地修改加-i）
sed -i '' "s/PORT=.*/PORT=$new_port/" .env    # macOS
sed -i "s/PORT=.*/PORT=$new_port/" .env        # Linux

# awk提取列
awk -F: '{print $1, $3}' /etc/passwd

# 读取文件逐行处理
while IFS= read -r line; do
    echo "处理: $line"
done < "$input_file"
```

### 交叉编译+上传
```bash
# Go交叉编译全平台
platforms=("linux/amd64" "linux/arm64" "darwin/amd64" "darwin/arm64")
for platform in "${platforms[@]}"; do
    IFS='/' read -r os arch <<< "$platform"
    output="build/${name}_${os}_${arch}"
    CGO_ENABLED=0 GOOS="$os" GOARCH="$arch" go build -ldflags="-s -w" -o "$output" .
done
```

## 安全规则

| 规则 | 正确 | 错误 | 原因 |
|------|------|------|------|
| 变量引号 | `"$var"` | `$var` | 空格/通配符导致分词 |
| 命令替换 | `"$(cmd)"` | `` `cmd` `` | 反引号嵌套困难 |
| 路径拼接 | `"${dir}/${file}"` | `$dir/$file` | dir为空时变成`/file`(根目录) |
| 删除安全 | `rm -rf "${dir:?}/"` | `rm -rf $dir/` | dir为空时删根目录 |
| 条件判断 | `[[ ]]` | `[ ]` | `[[`支持正则、不分词 |
| 临时文件 | `mktemp` | `/tmp/myapp.tmp` | 硬编码有竞争条件+安全风险 |
| 读取输入 | `read -r` | `read` | 无-r会处理反斜杠转义 |

## 调试技巧

```bash
# 显示执行的每条命令
set -x          # 开启
set +x          # 关闭

# 只打印不执行（dry run）
DRY_RUN=${DRY_RUN:-0}
run() {
    if (( DRY_RUN )); then
        log "[DRY RUN] $*"
    else
        "$@"
    fi
}

# 执行耗时统计
time_start=$(date +%s)
# ... 操作 ...
time_end=$(date +%s)
log "耗时: $((time_end - time_start))秒"
```

## 约束
- 所有脚本必须通过`shellcheck`检查（如可用）
- macOS和Linux差异必须注意（sed -i语法/readlink/date等）
- 脚本中的路径一律用变量，禁止散落的硬编码路径
- 密码/密钥不硬编码到脚本中，用环境变量或配置文件
