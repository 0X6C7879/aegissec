---
name: test-engineering
description: 测试工程技能 - TDD流程、测试策略、覆盖率、Mock规范、回归测试。当你涉及测试用例、覆盖率、TDD、Mock、断言、回归测试时必须使用此技能。即使用户只是说"加个测试"或"这个bug要测试"，也应触发。
---

# 测试工程技能 (Test Engineering Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[测试核心清单]** ① 修Bug必须先写失败测试→修复→测试通过（TDD红绿循环） ② 新功能至少4个用例：正常/边界/异常/权限 ③ Mock外部依赖，不Mock核心逻辑
> **[测试三禁]** ❌空断言(assert无实际检查) ❌注释掉的测试 ❌sleep等待异步(用await/信号量)
> **[命名铁律]** `test_<功能>_<场景>_<预期结果>`，测试名即文档，读名字就知道测什么

写/改涉及测试的代码时，强制遵守：
1. **TDD红绿循环**：修Bug→先写一个能复现Bug的失败测试→修复代码→测试通过→提交。禁止修完Bug不写测试（没有测试=同样的bug会反复出现，回归无保障）
2. **最少用例覆盖**：每个功能/端点至少覆盖4个场景：
   - 正常流程（Happy Path）
   - 边界条件（空值/零值/最大值/首次运行）
   - 异常流程（无效输入/网络错误/权限不足）
   - 权限/认证（未登录/无权限/越权访问）
3. **Mock原则**：只Mock外部依赖（网络/数据库/文件系统/第三方API），不Mock被测核心逻辑
4. **测试数据**：用工厂函数/Fixture生成，禁止硬编码魔数（魔数散落在测试中无法理解含义，改业务规则时要搜全项目改）。测试间数据隔离，不共享可变状态
5. **异步测试**：用`await`/`expectation`/信号量等待，❌禁止`sleep()`/`Thread.sleep()`等固定等待（固定等待=慢机器上随机失败，快机器上浪费时间，用事件驱动等待才可靠）
6. **并发测试**：涉及共享状态的操作必须有竞态测试（多线程同时操作同一资源）
7. **测试命名**：`test_<功能>_<场景>_<预期结果>`，如`test_login_wrongPassword_returns401`
8. **测试独立性**：每个测试可独立运行，不依赖执行顺序，setUp/tearDown清理状态

---

## 完整审查流程（手动 /test-engineering 或专项审查时执行）

### Phase 1: 测试现状扫描

1. 扫描项目测试文件：
   - 测试文件数量/位置/命名规范
   - 测试框架识别（XCTest/pytest/Jest/JUnit/go test等）
   - 测试配置（CI集成/覆盖率工具/Mock框架）

2. 测试覆盖度评估：
   - 哪些模块有测试/哪些没有
   - 核心业务逻辑的测试覆盖情况
   - 关键路径（认证/支付/权限）是否有测试

### Phase 2: 测试质量审计

3. 检查测试有效性：
   - **空断言**：测试函数中没有assert/expect语句 = 无效测试
   - **弱断言**：只检查"不为nil"但不检查具体值 = 低质量测试
   - **注释掉的测试**：被注释/跳过(@Disabled/@Skip)的测试 = 技术债
   - **重复测试**：多个测试验证相同场景 = 浪费维护成本
   - **过度Mock**：Mock了被测对象的核心逻辑 = 测试无意义

4. 检查测试可维护性：
   - 测试辅助函数/工厂是否复用（而非每个测试重复构造数据）
   - 测试命名是否清晰表达意图
   - 测试是否过度依赖实现细节（改内部实现就要改测试=脆弱测试）

### Phase 3: 测试策略审查

5. 测试金字塔评估：

| 层级 | 占比目标 | 特点 | 验证内容 |
|------|----------|------|----------|
| 单元测试 | 70% | 快速/隔离/数量多 | 函数逻辑、边界条件、错误处理 |
| 集成测试 | 20% | 中速/跨模块 | API端到端、数据库CRUD、服务间交互 |
| E2E测试 | 10% | 慢/全链路 | 核心用户流程、关键业务场景 |

   - 当前项目的测试分布是否符合金字塔？
   - 是否存在"倒金字塔"（只有E2E没有单元测试）？

6. 边界测试检查清单：
   - 空集合/空字符串/nil/null
   - 零值/负数/最大值/整数溢出
   - 空格/特殊字符/超长字符串/Unicode
   - 首次运行/无历史数据
   - 并发访问同一资源
   - 网络超时/断开/慢速

### Phase 4: 安全测试审查

7. 安全相关测试覆盖：
   - 认证测试：未登录/Token过期/Token伪造/刷新Token
   - 授权测试：越权访问(水平+垂直)/资源所有权验证
   - 输入验证测试：SQL注入参数/XSS参数/路径穿越/超大请求
   - 业务逻辑测试：负数金额/零元购/重复提交/并发扣减
   - 速率限制测试：暴力破解/频繁请求

### Phase 5: 回归测试策略

8. 回归测试机制：
   - 每次Bug修复必须附带回归测试（防止同类Bug再次出现）
   - 关键路径的冒烟测试（每次发布前自动运行）
   - 测试失败必须阻断CI/CD（不允许忽略失败的测试）

9. 测试数据管理：
   - 测试数据隔离策略（每个测试独立数据/事务回滚/容器化）
   - 敏感数据脱敏（测试中不使用真实用户数据）
   - 大数据量测试（性能边界验证）

### Phase 5.5: macOS/Apple平台测试专项

10. XCTest单元测试：
    - 测试类继承`XCTestCase`，命名`<被测类>Tests`
    - `setUp()`准备测试环境，`tearDown()`清理（用`addTeardownBlock`确保异常时也清理）
    - 异步测试用`XCTestExpectation` + `wait(for:timeout:)`，❌禁止`sleep()`（固定等待=慢机器随机失败+快机器浪费时间）
    - Combine测试用`sink` + expectation，SwiftUI测试用`ViewInspector`或快照测试

11. XCUITest端到端测试（macOS/iOS UI自动化）：
    - 用`XCUIApplication().launch()`启动被测应用
    - 元素定位优先级：`accessibilityIdentifier` > `label` > `index`（identifier最稳定）
    - 等待元素：`element.waitForExistence(timeout: 5)`，❌禁止固定sleep（固定sleep在CI环境下极不稳定，用事件驱动等待）
    - 关键流程必须覆盖：启动→核心功能→设置→异常恢复
    - 测试间重置应用状态：`launchArguments.append("--uitesting")`

12. macOS特有测试场景：
    - 沙盒权限测试：Entitlements声明的权限是否正确生效
    - 多窗口测试：新建/关闭/切换窗口的状态一致性
    - 菜单栏/快捷键测试：所有菜单项可达，快捷键不冲突
    - AppleScript/Automation权限测试（如有）
    - 深色/浅色模式切换后UI是否正确响应
    - Retina/非Retina显示适配

### Phase 6: 输出报告

```
## 测试审查报告

### 测试覆盖概览
| 模块 | 测试文件 | 用例数 | 覆盖场景 | 缺失场景 | 优先级 |

### 测试质量问题
| # | 文件:行号 | 问题类型 | 描述 | 修复建议 | 优先级 |

### 缺失测试（按风险排序）
| # | 模块/功能 | 缺失测试类型 | 风险 | 建议用例 | 优先级 |

### 测试策略建议
| 类别 | 当前状态 | 建议 | 预期效果 |

### 测试金字塔评估
| 层级 | 当前数量 | 目标占比 | 实际占比 | 调整建议 |
```

## 测试开发规则（写测试代码时强制遵守）

### 写测试前必须执行
1. 确认被测函数/模块的所有输入输出
2. 列出所有边界条件和异常场景
3. 确认Mock范围（只Mock外部依赖）

### 写测试时强制规则
- ❌ 禁止空断言（测试永远绿灯但什么都没验证，给人虚假安全感）
- ❌ 禁止sleep等待异步（等短了偶发失败，等长了浪费CI时间，用await/expectation精确等待）
- ❌ 禁止测试间共享可变状态（测试执行顺序变化就随机失败，难以复现和定位）
- ❌ 禁止注释掉测试代替删除（注释的测试是死代码，无人维护且掩盖覆盖率真相）
- ❌ 禁止Mock核心逻辑（Mock了被测对象=测试只验证了Mock本身，真实逻辑完全未覆盖）
- ✅ 每个Bug修复必须附带回归测试
- ✅ 测试命名必须表达意图：`test_<功能>_<场景>_<预期>`
- ✅ 测试数据用工厂/Fixture，不硬编码魔数

## 框架模板速查

### Go test（标准库）
```go
func TestCreateUser_ValidInput_ReturnsUser(t *testing.T) {
    // Arrange
    input := CreateUserInput{Name: "test", Email: "test@example.com"}

    // Act
    user, err := CreateUser(input)

    // Assert
    if err != nil {
        t.Fatalf("expected no error, got %v", err)
    }
    if user.Name != input.Name {
        t.Errorf("expected name %q, got %q", input.Name, user.Name)
    }
}

// Table-driven tests（Go推荐模式）
func TestParse(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    int
        wantErr bool
    }{
        {"valid", "42", 42, false},
        {"negative", "-1", -1, false},
        {"empty", "", 0, true},
        {"non-numeric", "abc", 0, true},
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

// HTTP handler测试
func TestGetUser_NotFound_Returns404(t *testing.T) {
    req := httptest.NewRequest("GET", "/api/users/999", nil)
    w := httptest.NewRecorder()
    handler.ServeHTTP(w, req)
    if w.Code != http.StatusNotFound {
        t.Errorf("expected 404, got %d", w.Code)
    }
}
```

### Vitest（Vue/JS/TS）
```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'

describe('createUser', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('正常创建返回用户', async () => {
    const input = { name: 'test', email: 'test@example.com' }
    const user = await createUser(input)
    expect(user.name).toBe('test')
    expect(user.id).toBeDefined()
  })

  it('缺少必填字段抛出错误', async () => {
    await expect(createUser({ name: '' })).rejects.toThrow('name is required')
  })

  it('重复邮箱返回409', async () => {
    vi.spyOn(db, 'findByEmail').mockResolvedValue({ id: 1 })
    await expect(createUser({ name: 'a', email: 'dup@x.com' }))
      .rejects.toThrow('email already exists')
  })
})

// Vue组件测试
import { mount } from '@vue/test-utils'
import UserCard from '@/components/UserCard.vue'

describe('UserCard', () => {
  it('显示用户名', () => {
    const wrapper = mount(UserCard, { props: { name: '张三' } })
    expect(wrapper.text()).toContain('张三')
  })

  it('点击删除触发事件', async () => {
    const wrapper = mount(UserCard, { props: { name: '张三' } })
    await wrapper.find('.delete-btn').trigger('click')
    expect(wrapper.emitted('delete')).toBeTruthy()
  })
})
```

### 常用测试命令
```bash
# Go
go test ./... -v -race -cover                    # 全量测试+竞态+覆盖率
go test -run TestCreateUser ./handler/            # 单个测试
go test -coverprofile=cover.out && go tool cover -html=cover.out  # 覆盖率报告

# Vitest
npx vitest run                                    # 全量测试
npx vitest run --coverage                         # 覆盖率
npx vitest run src/utils/time.test.ts             # 单个文件
npx vitest --watch                                # 监听模式
```

## 约束
- 测试建议必须附带具体的用例描述（输入→预期输出）
- 缺失测试按风险排序（认证>支付>权限>核心业务>辅助功能）
- 不追求100%覆盖率——关注关键路径和边界条件
- 测试框架选择遵循项目已有约定，不引入额外测试框架
