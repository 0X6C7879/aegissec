---
name: apple-development
description: Apple原生开发技能 - Swift/SwiftUI/AppKit/UIKit原生组件、API选择、权限配置、图标规格、Liquid Glass、上架规则、macOS/iOS平台特性。当你涉及Swift/SwiftUI/AppKit/UIKit原生开发、Apple平台特性、Xcode配置、权限entitlements、图标资源、App Store上架、Liquid Glass效果时必须使用此技能。即使用户只是说"用原生的"或"加个SwiftUI视图"，也应触发。
---

# Apple原生开发技能 (Apple Development Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[原生核心清单]** ① 优先用SwiftUI原生组件（List/NavigationStack/TabView/Sheet），❌禁止自己造轮子重写系统组件 ② 用系统API而非第三方库（URLSession/Keychain/UserDefaults/Core Data） ③ 权限按需申请+Info.plist描述必须清晰具体
> **[平台三禁]** ❌UIKit组件混入纯SwiftUI项目（除非SwiftUI确实无法实现） ❌硬编码设备尺寸/安全区域 ❌忽略深色模式/Dynamic Type适配
> **[上架铁律]** 隐私政策必须可访问 | IAP必须有恢复购买 | 登录必须支持Sign in with Apple | 截图必须真实

写/改Apple原生代码时，强制遵守：
1. **组件选择**：优先SwiftUI原生组件，禁止重写系统已有组件（重写的组件无法跟随系统更新，且缺少无障碍支持）
2. **API选择**：优先系统框架（URLSession>Alamofire, Keychain>自定义加密存储, async/await>Combine>回调）
3. **生命周期**：SwiftUI用`@main`+`App`协议，UIKit用`SceneDelegate`，macOS用`NSApplicationDelegate`
4. **状态管理**：`@State`局部 → `@StateObject`拥有 → `@ObservedObject`引用 → `@EnvironmentObject`全局，禁止滥用`@EnvironmentObject`传递所有状态（全局状态过多=任何变化触发全局刷新，性能灾难）
5. **并发模型**：Swift Concurrency（async/await + Actor）优先，禁止新代码用GCD回调嵌套（回调地狱=可读性差且容易遗漏错误处理）
6. **权限管理**：按需申请（用时才弹），Info.plist的Usage Description必须具体说明用途
7. **适配**：必须支持深色模式+Dynamic Type+无障碍（VoiceOver/Switch Control）
8. **图标**：使用SF Symbols系统图标，禁止用emoji或自制低质量图标（SF Symbols自动适配所有尺寸/粗细/无障碍）

---

## 完整审查流程（手动 /apple-development 或专项审查时执行）

### Phase 1: 项目配置审查

1. **Xcode项目结构**：
   - Bundle Identifier格式：`com.company.appname`
   - Deployment Target设置合理（建议最低支持前2个大版本）
   - Build Settings中Swift Language Version正确
   - Signing配置：开发用Automatic，发布用Manual指定证书和Profile

2. **Info.plist关键配置**：
   - `CFBundleDisplayName`：用户可见的应用名
   - `NSHumanReadableCopyright`：版权声明
   - `LSMinimumSystemVersion` (macOS) / `MinimumOSVersion` (iOS)
   - 所有权限Usage Description必须填写且具体（模糊描述=审核被拒）

3. **Entitlements权限**：

| 权限 | Entitlement Key | 说明 |
|------|----------------|------|
| 网络（客户端） | `com.apple.security.network.client` | 发起网络请求 |
| 网络（服务端） | `com.apple.security.network.server` | 监听端口 |
| 文件读写 | `com.apple.security.files.user-selected.read-write` | 用户选择的文件 |
| 下载文件夹 | `com.apple.security.files.downloads.read-write` | Downloads目录 |
| 摄像头 | `com.apple.security.device.camera` | 使用摄像头 |
| 麦克风 | `com.apple.security.device.audio-input` | 使用麦克风 |
| 位置 | `com.apple.security.personal-information.location` | 位置信息 |
| 通讯录 | `com.apple.security.personal-information.addressbook` | 通讯录 |
| 日历 | `com.apple.security.personal-information.calendars` | 日历数据 |
| Apple Events | `com.apple.security.automation.apple-events` | 控制其他应用 |
| Keychain | `keychain-access-groups` | Keychain访问组 |
| App Groups | `com.apple.security.application-groups` | 应用间共享数据 |
| Hardened Runtime | `com.apple.security.cs.disable-library-validation` | 允许加载第三方库 |
| 辅助功能 | `com.apple.security.accessibility` | 辅助功能API |

### Phase 2: SwiftUI原生组件速查

4. **导航**：
   - `NavigationStack` (iOS 16+) 替代 `NavigationView`（NavigationView已废弃）
   - `NavigationSplitView` (iOS 16+/macOS 13+)：侧边栏+详情双栏布局
   - `.navigationTitle()` + `.navigationBarTitleDisplayMode()`
   - `.toolbar { ToolbarItem(placement:) { } }`

5. **列表与数据展示**：
   - `List` + `ForEach`：标准列表，自带滚动/选择/滑动操作
   - `Table` (macOS)：多列表格，支持排序
   - `LazyVStack`/`LazyHStack`：懒加载栈（大量数据用这个）
   - `Grid` (iOS 16+)：网格布局
   - `Section` + `DisclosureGroup`：分组和折叠

6. **输入与表单**：
   - `TextField` / `SecureField` / `TextEditor`
   - `Picker` / `DatePicker` / `ColorPicker` / `Stepper` / `Slider` / `Toggle`
   - `Form`：设置页面标准容器
   - `.onSubmit { }` 处理回车提交

7. **弹窗与浮层**：
   - `.sheet()` / `.fullScreenCover()`：模态视图
   - `.alert()` / `.confirmationDialog()`：系统弹窗
   - `.popover()`：悬浮气泡(iPad/macOS)
   - `.inspector()`(macOS 14+)：侧边检查器
   - `.fileImporter()` / `.fileExporter()`：文件选择

8. **系统集成**：
   - `ShareLink`：系统分享
   - `PhotosPicker`：照片选择器
   - `.onDrag()` / `.onDrop()`：拖放
   - `.onCopyCommand()` / `.onPasteCommand()`：复制粘贴
   - `MenuBarExtra`(macOS)：菜单栏图标应用
   - `Settings`(macOS)：偏好设置窗口

### Phase 3: 常用系统API速查

9. **网络**：
   - `URLSession.shared.data(from:)` (async) — 简单GET
   - `URLSession.shared.upload(for:from:)` (async) — 上传
   - `URLSession.shared.download(from:)` (async) — 下载
   - 后台下载：`URLSessionConfiguration.background(withIdentifier:)`

10. **数据持久化**：
    - `UserDefaults`：小量设置数据（❌禁止存大数据/敏感数据，UserDefaults是plist明文存储）
    - `Keychain`：密码/Token/密钥（用Security框架或KeychainAccess库）
    - `FileManager`：文件系统操作
    - `SwiftData` (iOS 17+/macOS 14+) 替代 Core Data
    - `@AppStorage`：SwiftUI的UserDefaults绑定

11. **系统服务**：
    - `UNUserNotificationCenter`：本地/远程通知
    - `CLLocationManager`：位置服务
    - `NWPathMonitor`：网络状态监听
    - `ProcessInfo`：系统信息（热保护状态/低电量模式）
    - `NSWorkspace` (macOS)：打开URL/文件/应用
    - `NSAppleScript` (macOS)：执行AppleScript
    - `SMAppService` (macOS)：登录项管理

### Phase 4: iOS 26 / macOS Tahoe 新特性

12. **Liquid Glass效果**（iOS 26/macOS 26）：

**核心API**：
```swift
// 基本玻璃效果
.glassEffect()  // 默认: .regular变体, .capsule形状
.glassEffect(.regular, in: .capsule, isEnabled: true)

// 玻璃类型
Glass.regular   // 标准自适应（工具栏/按钮/导航栏）
Glass.clear     // 高透明（浮在媒体内容上的小控件）
Glass.identity  // 无效果（条件切换用）

// 修饰器
.tint(_ color: Color)  // 语义着色（仅用于主操作，不是装饰）
.interactive()         // 启用交互行为（仅iOS：按压缩放/弹跳/发光）

// 按钮样式
.buttonStyle(.glass)           // 半透明玻璃按钮
.buttonStyle(.glassProminent)  // 不透明突出按钮

// 容器（控制morphing距离）
GlassEffectContainer { /* 内含.glassEffect()的视图 */ }

// 形变动画
.glassEffectID(id, in: namespace)  // 标记morphing身份
.glassEffectUnion(id:namespace:)   // 合并多个玻璃元素
.glassEffectTransition()           // 玻璃过渡动画
```

**Liquid Glass使用规则**：
- ✅ 用于：导航栏/工具栏/TabBar/浮动按钮/Sheet/弹窗/菜单
- ❌ 禁止用于：内容层（列表/表格/媒体/滚动内容/全屏背景）（Liquid Glass是导航层效果，用于内容层破坏视觉层级）
- ❌ 禁止堆叠玻璃（glass-on-glass）（多层叠加导致可读性为零）
- ❌ 禁止给所有元素都加玻璃（过度使用=毫无重点，反而降低可用性）
- tint仅用于传达语义（主操作/状态），不是装饰
- 无障碍自动适配：Reduce Transparency/Increase Contrast/Reduce Motion自动响应

13. **向后兼容**：
```swift
// 条件使用Liquid Glass
if #available(iOS 26, macOS 26, *) {
    content.glassEffect()
} else {
    content.background(.ultraThinMaterial)
}
```

14. **Icon Composer**（Xcode 26+）：
    - 新的`.icon`文件格式替代传统Asset Catalog中的AppIcon
    - 支持多层图标设计（前景/背景分层）
    - 自动生成所有平台所需的图标尺寸

### Phase 5: 应用图标规格

15. **macOS图标**：
    - 必须提供1024×1024px母版（Xcode自动缩放其余尺寸）
    - 实际使用尺寸：16/32/64/128/256/512/1024（各含@1x和@2x）
    - macOS图标可以是任意形状（不像iOS强制圆角矩形）
    - 建议使用Icon Composer创建多层图标

16. **iOS图标**：
    - 必须提供1024×1024px母版
    - iOS自动添加圆角矩形裁剪，❌禁止在图标中手动加圆角（系统会双重裁剪导致变形）
    - iOS 26+支持Icon Composer多层图标
    - 不得包含透明度（iOS会用黑色填充透明区域）

17. **通用图标要求**：
    - 不含alpha通道（iOS）/ 可含alpha通道（macOS）
    - 色彩空间：sRGB或Display P3
    - 格式：PNG
    - ❌禁止使用截图/照片作为图标（审核会拒绝，且缩小后不可辨识）
    - ❌禁止与其他应用图标过于相似（侵权风险）

### Phase 6: App Store上架检查清单

18. **审核必过清单**：

| 类别 | 要求 | 常见被拒原因 |
|------|------|-------------|
| 隐私 | Privacy Policy链接可访问且内容完整 | 链接404/内容不匹配 |
| 权限 | 每个权限有清晰的Usage Description | 描述模糊如"需要访问您的照片" |
| IAP | 有恢复购买(Restore Purchases)按钮 | 缺少恢复按钮 |
| 登录 | 支持Sign in with Apple（如有第三方登录） | 有Google登录但无Apple登录 |
| 内容 | 年龄分级准确 | 实际内容超出声明的年龄分级 |
| 元数据 | 截图真实反映应用功能 | 截图有误导性 |
| 完整性 | 所有功能可正常使用 | 有placeholder页面/崩溃/功能不完整 |
| 审核账号 | 提供测试账号和说明 | 审核员无法测试需登录的功能 |
| 链接 | 支持/隐私/使用条款链接均有效 | 链接指向开发中的页面 |

19. **macOS特有要求**：
    - 沙盒化（App Store分发必须）
    - 签名+公证（Notarization）— 非App Store也需要
    - 支持Apple Silicon原生运行
    - 菜单栏必须有标准菜单项（文件/编辑/窗口/帮助）
    - 支持系统快捷键（⌘Q退出/⌘W关闭窗口/⌘,偏好设置）

20. **提交前自检命令**：
```bash
# 验证签名
codesign -dvvv /path/to/App.app
# 验证公证
spctl -a -vvv /path/to/App.app
# 检查entitlements
codesign -d --entitlements :- /path/to/App.app
# 检查架构
lipo -info /path/to/App.app/Contents/MacOS/AppName
# 检查最低系统版本
otool -l /path/to/binary | grep -A2 LC_BUILD_VERSION
```

### Phase 7: 性能与最佳实践

21. **SwiftUI性能**：
    - 视图body应该是纯函数，❌禁止在body中做IO/网络/重计算（body可能被频繁调用，重操作=卡顿）
    - 大列表用`LazyVStack`/`LazyHStack`，❌禁止用`VStack`加载1000+项（VStack一次加载所有项=OOM）
    - `@State`/`@StateObject`精细化，避免大对象导致不必要的刷新
    - 图片用`.resizable()`+`.aspectRatio()`+`AsyncImage`异步加载
    - 复杂视图拆分为子视图减少diff计算量

22. **内存管理**：
    - 闭包中用`[weak self]`防循环引用
    - `Timer`/`NotificationCenter`观察者在`deinit`中取消
    - 大图片用`CGImageSource`降采样而非`UIImage(named:)`全分辨率加载
    - `@StateObject`只在拥有者视图创建，子视图用`@ObservedObject`

23. **macOS特有注意事项**：
    - `NSWindow`管理：注意窗口生命周期和内存释放
    - `NSScreen`坐标系：左下角为原点（↗），与iOS左上角（↘）相反
    - `NSEvent`监听：全局事件监听需要辅助功能权限
    - `NSStatusItem`：菜单栏图标应用
    - `NSAppleScript`/`Process`：执行系统命令时注意沙盒限制

### Phase 8: Swift语言最佳实践

24. **现代Swift特性优先使用**：
    - `async/await` > Combine > 回调（可读性和错误处理优先）
    - `Result`类型处理错误 > throws（当需要存储/传递错误时）
    - `Codable` 自动编解码 > 手动JSON解析
    - `String`插值 > `String(format:)`
    - `guard let` 早返回 > 多层`if let`嵌套
    - `enum`关联值 > 多个可选属性的组合

25. **命名规范**（Apple官方Swift API Design Guidelines）：
    - 类型名：`UpperCamelCase`（`UserProfile`）
    - 函数/变量名：`lowerCamelCase`（`fetchUserData`）
    - 布尔属性：`is`/`has`/`should`前缀（`isLoading`/`hasError`）
    - 协议名：描述能力用`-able`/`-ible`（`Codable`），描述身份用名词（`Collection`）
    - 工厂方法：`make`前缀（`makeIterator()`）

## 约束
- 始终优先使用Apple原生API和组件，第三方库是最后选择
- 新代码必须使用Swift Concurrency（async/await），禁止新写GCD回调嵌套（回调嵌套=可读性差+易遗漏错误处理+难以取消）
- Liquid Glass仅用于导航层控件，禁止用于内容层（内容层加玻璃=视觉层级混乱，违反Apple HIG）
- 每个权限申请必须有对应的Info.plist Usage Description，禁止空描述或模糊描述（审核必拒+用户不信任）
- 图标必须使用SF Symbols或自制高质量图标，禁止emoji或低质量图片（emoji跨平台不一致且不专业）
- 上架前必须完成Phase 6全部检查项，禁止跳过任何一项（跳过=审核被拒浪费1-7天）
- macOS应用必须支持标准菜单栏和系统快捷键（⌘Q/⌘W/⌘,），禁止只有自定义快捷键（违反macOS HIG，用户找不到基本操作）
