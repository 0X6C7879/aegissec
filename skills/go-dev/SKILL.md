---
name: go-dev
description: Go语言开发技能 - Go语法、并发编程、goroutine/channel、错误处理、模块管理、测试、性能优化。当你涉及Go/Golang代码开发、goroutine、channel、go mod、接口设计、泛型、struct设计时必须使用此技能。即使用户只是说"写个Go函数"或"改下Go代码"，也应触发。
---

# Go语言开发技能 (Go Development Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[Go核心清单]** ① error必须处理：`if err != nil`后return/log+return/wrap，禁止`_ = fn()` ② 并发map用`sync.RWMutex`或`sync.Map`，goroutine必须有退出机制(context/done channel) ③ 字符串拼接用`strings.Builder`，slice预分配`make([]T, 0, cap)`
> **[三禁]** ❌裸传error(无上下文) ❌goroutine无退出条件(泄漏) ❌`init()`中做复杂逻辑(难测试+隐式依赖)
> **[命名铁律]** 导出用MixedCaps无下划线，接口-er后缀(Reader/Writer)，包名小写单词无下划线

写/改Go代码时，强制遵守：
1. **错误处理**：每个error必须处理，用`fmt.Errorf("context: %w", err)`附加上下文，禁止裸传err
2. **并发安全**：map/slice并发读写必须加锁，goroutine必须有context取消或done channel退出
3. **资源释放**：打开的资源(DB/File/HTTP Body)必须`defer xxx.Close()`
4. **接口设计**：接口定义在使用方而非实现方，接口方法≤3个，优先小接口组合
5. **命名规范**：包名小写单词（`httputil`非`http_util`），导出函数MixedCaps，接口用-er后缀
6. **zero value有用**：struct设计让零值可用（如`sync.Mutex`零值即可用）
7. **context传播**：第一个参数传`ctx context.Context`，用于超时控制和取消传播

---

## 错误处理模式

### 标准模式
```go
// ✅ 正确：附加上下文
result, err := db.Query(ctx, sql, args...)
if err != nil {
    return fmt.Errorf("查询用户列表: %w", err)
}

// ❌ 错误：裸传
if err != nil {
    return err  // 调用链长时无法定位出错位置
}

// ❌ 错误：忽略
_ = db.Close()  // 可能丢失flush数据
```

### 哨兵错误
```go
// 定义包级哨兵错误
var (
    ErrNotFound    = errors.New("not found")
    ErrUnauthorized = errors.New("unauthorized")
)

// 检查
if errors.Is(err, ErrNotFound) {
    // 处理未找到
}

// 类型断言
var appErr *AppError
if errors.As(err, &appErr) {
    log.Printf("code=%d msg=%s", appErr.Code, appErr.Message)
}
```

### defer中的错误处理
```go
func writeFile(path string, data []byte) (retErr error) {
    f, err := os.Create(path)
    if err != nil {
        return fmt.Errorf("创建文件: %w", err)
    }
    defer func() {
        if cerr := f.Close(); cerr != nil && retErr == nil {
            retErr = fmt.Errorf("关闭文件: %w", cerr)
        }
    }()
    _, err = f.Write(data)
    return err
}
```

## 并发模式

### goroutine + WaitGroup
```go
var wg sync.WaitGroup
errCh := make(chan error, len(items))

for _, item := range items {
    wg.Add(1)  // Add在goroutine外！
    go func(it Item) {
        defer wg.Done()
        if err := process(ctx, it); err != nil {
            errCh <- err
        }
    }(item)
}

wg.Wait()
close(errCh)

for err := range errCh {
    log.Printf("处理失败: %v", err)
}
```

### context取消
```go
ctx, cancel := context.WithTimeout(parentCtx, 5*time.Second)
defer cancel()

select {
case result := <-doWork(ctx):
    return result, nil
case <-ctx.Done():
    return nil, ctx.Err()
}
```

### 安全的并发map
```go
// 方式1：sync.RWMutex（读多写少）
type SafeMap struct {
    mu sync.RWMutex
    m  map[string]int
}
func (s *SafeMap) Get(key string) (int, bool) {
    s.mu.RLock()
    defer s.mu.RUnlock()
    v, ok := s.m[key]
    return v, ok
}
func (s *SafeMap) Set(key string, val int) {
    s.mu.Lock()
    defer s.mu.Unlock()
    s.m[key] = val
}

// 方式2：sync.Map（key稳定的场景）
var cache sync.Map
cache.Store("key", value)
if v, ok := cache.Load("key"); ok { ... }
```

### Worker Pool
```go
func workerPool(ctx context.Context, jobs <-chan Job, workers int) <-chan Result {
    results := make(chan Result, workers)
    var wg sync.WaitGroup

    for i := 0; i < workers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for job := range jobs {
                select {
                case <-ctx.Done():
                    return
                case results <- process(job):
                }
            }
        }()
    }

    go func() {
        wg.Wait()
        close(results)
    }()

    return results
}
```

## 设计模式

### Functional Options
```go
type Server struct {
    port    int
    timeout time.Duration
    logger  *log.Logger
}

type Option func(*Server)

func WithPort(p int) Option       { return func(s *Server) { s.port = p } }
func WithTimeout(d time.Duration) Option { return func(s *Server) { s.timeout = d } }

func NewServer(opts ...Option) *Server {
    s := &Server{port: 8080, timeout: 30 * time.Second}  // 默认值
    for _, opt := range opts {
        opt(s)
    }
    return s
}
```

### Table-Driven Tests
```go
func TestParse(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    int
        wantErr bool
    }{
        {"正常", "42", 42, false},
        {"负数", "-1", -1, false},
        {"空串", "", 0, true},
        {"非数字", "abc", 0, true},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := Parse(tt.input)
            if (err != nil) != tt.wantErr {
                t.Errorf("Parse(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
                return
            }
            if got != tt.want {
                t.Errorf("Parse(%q) = %v, want %v", tt.input, got, tt.want)
            }
        })
    }
}
```

### 接口设计
```go
// ✅ 小接口，使用方定义
type Reader interface {
    Read(p []byte) (n int, err error)
}

// ✅ 接口组合
type ReadWriter interface {
    Reader
    Writer
}

// ❌ 大而全的接口（违反接口隔离）
type Repository interface {
    Create(ctx context.Context, item Item) error
    Update(ctx context.Context, item Item) error
    Delete(ctx context.Context, id int) error
    FindByID(ctx context.Context, id int) (*Item, error)
    FindAll(ctx context.Context) ([]Item, error)
    Count(ctx context.Context) (int, error)
    // ... 20个方法
}
```

## 性能优化

### 字符串
```go
// ✅ strings.Builder（循环拼接）
var b strings.Builder
for _, s := range items {
    b.WriteString(s)
}
result := b.String()

// ❌ 循环中用+拼接（每次分配新内存）
result := ""
for _, s := range items {
    result += s
}
```

### Slice
```go
// ✅ 预分配容量
users := make([]User, 0, len(ids))
for _, id := range ids {
    users = append(users, fetchUser(id))
}

// ❌ 零容量反复扩容
var users []User  // cap=0，每次append可能重新分配
```

### HTTP客户端复用
```go
// ✅ 全局复用（连接池）
var httpClient = &http.Client{
    Timeout: 10 * time.Second,
    Transport: &http.Transport{
        MaxIdleConns:        100,
        MaxIdleConnsPerHost: 10,
        IdleConnTimeout:     90 * time.Second,
    },
}

// ❌ 每次请求新建
func fetch(url string) {
    client := &http.Client{}  // 无法复用连接
    client.Get(url)
}
```

### 避免反射
```go
// 热路径避免reflect和interface{}断言
// JSON编解码在热路径考虑用 json-iterator 或 sonic 替代标准库
```

## 项目结构

```
project/
├── cmd/api/main.go       # 入口
├── config/               # 配置加载
├── handler/              # HTTP handler（入参校验+响应格式化）
├── service/              # 业务逻辑
├── model/                # 数据模型+DB操作
├── middleware/            # 中间件（认证/限流/CORS/日志）
├── pkg/                  # 可复用库（被外部引用的）
├── internal/             # 内部包（禁止外部引用）
├── go.mod
└── go.sum
```

## Go模块管理
```bash
go mod init module-name   # 初始化
go mod tidy               # 清理未用依赖+补充缺失依赖
go mod vendor             # 生成vendor目录（离线构建用）
go get -u ./...           # 更新所有依赖
go list -m all            # 列出所有依赖
```

## Go 1.22+ 现代特性

### Enhanced ServeMux (Go 1.22)
```go
mux := http.NewServeMux()
mux.HandleFunc("GET /api/users/{id}", func(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")  // 内置路径参数提取
    // ...
})
mux.HandleFunc("POST /api/users", createUser)
mux.HandleFunc("DELETE /api/users/{id}", deleteUser)
// 方法+路径模式，不再需要手动检查r.Method
```

### range over int (Go 1.22)
```go
for i := range 10 { fmt.Println(i) }  // 0~9，替代 for i := 0; i < 10; i++
```

### log/slog 结构化日志 (Go 1.21+)
```go
import "log/slog"

// 替代log.Printf，结构化输出便于日志收集
slog.Info("请求处理", "method", r.Method, "path", r.URL.Path, "duration", elapsed)
slog.Error("查询失败", "err", err, "sql", query)

// 自定义handler
logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
slog.SetDefault(logger)
```

## 高级并发模式

### errgroup（并发+错误收集）
```go
import "golang.org/x/sync/errgroup"

g, ctx := errgroup.WithContext(ctx)
g.SetLimit(10)  // 限制并发数

for _, url := range urls {
    g.Go(func() error {
        return fetch(ctx, url)
    })
}
if err := g.Wait(); err != nil {
    return fmt.Errorf("批量请求失败: %w", err)
}
```

### singleflight（去重并发请求）
```go
import "golang.org/x/sync/singleflight"

var group singleflight.Group

// 多个goroutine同时请求同一key，只执行一次
result, err, _ := group.Do(cacheKey, func() (interface{}, error) {
    return db.Query(ctx, "SELECT ...")  // 只执行一次
})
```

### 并发模式速查
| 模式 | 场景 | 关键构件 |
|------|------|----------|
| Fan-out/Fan-in | 并行处理+汇总 | goroutine + channel |
| Worker Pool | 限制并发数 | buffered chan + N goroutine |
| Pipeline | 流式多阶段处理 | chain of channels |
| Context取消 | 超时/取消传播 | context.WithTimeout/Cancel |
| errgroup | 并发+错误收集+限并发 | golang.org/x/sync/errgroup |
| singleflight | 去重并发请求（缓存击穿防护） | golang.org/x/sync/singleflight |
| semaphore | 加权信号量 | golang.org/x/sync/semaphore |

## 常用命令
```bash
go build -o bin/app ./cmd/myapp        # 构建
go test ./... -v -race -cover          # 测试+竞态检测+覆盖率
go test -bench=. -benchmem ./...       # 基准测试
go tool pprof http://localhost:6060/debug/pprof/profile  # CPU profiling
GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o app  # 交叉编译+瘦身
golangci-lint run                      # 综合lint（推荐配置.golangci.yml）
```

## Gin中间件最佳实践（从生产项目提取）

### 中间件速查表
| 中间件 | 核心模式 | 关键实现 |
|--------|----------|----------|
| JWT认证 | x-token Header→解析→黑名单检查→c.Set("claims") | 内存缓存黑名单O(1)查询 |
| JWT续期 | 快过期时c.Header("new-token")返回新token | 前端拦截器自动更新 |
| RBAC权限 | Casbin: enforcer.Enforce(roleID, path, method) | 权限与代码解耦，运行时动态修改 |
| Panic Recovery | recover()→区分broken pipe→写DB错误表+日志 | 便于后台管理查看，不只是日志 |
| 操作审计 | 非GET请求记录body/resp/耗时/IP到DB | io.ReadAll+NopCloser回写body，截断1KB防膨胀 |
| IP限流 | Redis TxPipeline(Incr+Expire)原子操作 | 无Redis时静默降级，返回剩余等待时间 |
| 请求超时 | context.WithTimeout+goroutine+select | buffered chan防泄漏，区分panic和超时 |

### JWT自动续期（关键代码）
```go
// 中间件中：快过期时通过response header返回新token
c.Set("claims", claims)
if claims.ExpiresAt.Unix()-time.Now().Unix() < claims.BufferTime {
    newToken, _ := refreshToken(token, claims)
    c.Header("new-token", newToken)
    c.Header("new-expires-at", strconv.FormatInt(newExp, 10))
}
```

### 请求超时（关键模式）
```go
// context.WithTimeout + goroutine + select 三板斧
ctx, cancel := context.WithTimeout(c.Request.Context(), timeout)
defer cancel()
c.Request = c.Request.WithContext(ctx)
done := make(chan struct{}, 1) // buffered防泄漏
go func() {
    defer func() { done <- struct{}{} }()
    c.Next()
}()
select {
case <-done: return
case <-ctx.Done():
    c.AbortWithStatusJSON(504, gin.H{"msg": "请求超时"})
}
```

### 操作审计日志（关键模式）
```go
// 读取body后必须回写，否则handler读不到
body, _ = io.ReadAll(c.Request.Body)
c.Request.Body = io.NopCloser(bytes.NewBuffer(body))
// 用responseBodyWriter包装c.Writer捕获响应体
// 截断>1KB的body/resp防止DB膨胀
```

### 服务分组架构（enter.go模式）
```go
// 统一注册点：api/service/router各层都用enter.go聚合
var ApiGroupApp = new(ApiGroup)
type ApiGroup struct { UserApi; OrderApi }

var ServiceGroupApp = new(ServiceGroup)
type ServiceGroup struct { UserService; OrderService }

// 使用时直接引用全局变量
var userService = service.ServiceGroupApp.UserService
```

## 约束
- 所有发现必须有file:line引用
- 遵循Go官方Code Review Comments: https://go.dev/wiki/CodeReviewComments
- 优先标准库，不引入不必要的第三方依赖
- 错误消息用小写开头、无标点结尾（Go惯例）
- 保持项目现有的代码风格，不引入新范式
