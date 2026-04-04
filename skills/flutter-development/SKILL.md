---
name: flutter-development
description: Flutter跨端开发技能 - Dart语言、Widget组件、状态管理、平台通道(MethodChannel/EventChannel/Pigeon/FFI)、硬件对接(BLE/传感器/USB)、性能优化、常见坑、打包发布。当你涉及Flutter/Dart代码开发、跨平台UI、平台通道、原生插件、硬件通信、Flutter打包发布时必须使用此技能。即使用户只是说"Flutter加个功能"或"跨端开发"，也应触发。
---

# Flutter跨端开发技能 (Flutter Development Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[Flutter核心清单]** ① Widget树保持浅层，提取子Widget而非巨型build() ② 状态管理选型统一（Riverpod/Bloc/Provider三选一），❌禁止混用多种方案 ③ 平台通道优先Pigeon类型安全代码生成，❌禁止手写字符串key的MethodChannel
> **[跨端三禁]** ❌在build()中做IO/网络/重计算（build可能每帧调用，阻塞=卡顿） ❌忽略平台差异直接假设行为一致 ❌硬编码像素值不用MediaQuery/LayoutBuilder适配
> **[硬件铁律]** 硬件通信必须有超时+重试+断连处理 | BLE操作必须在连接状态检查后执行 | 传感器数据必须节流(throttle)

写/改Flutter代码时，强制遵守：
1. **Widget拆分**：单个build()不超过80行，超过→提取为独立Widget/方法。禁止500行的build()（巨型build=diff计算慢+无法局部刷新+不可维护）
2. **状态管理**：项目统一一种方案，禁止混用Provider+Bloc+GetX（混用=状态流转不可追踪，debug噩梦）
3. **平台通道**：优先Pigeon代码生成→dart:ffi(C/C++)→MethodChannel手写。禁止手写字符串key的MethodChannel不做类型检查（字符串typo=运行时崩溃且编译器无法捕获）
4. **异步处理**：所有Future必须处理错误（`.catchError`或try-catch），禁止裸Future不处理异常（未处理的Future异常=静默失败，数据丢失用户无感知）
5. **生命周期**：StatefulWidget中initState初始化，dispose释放（Timer/StreamSubscription/AnimationController），禁止不dispose（内存泄漏+后台持续消耗资源）
6. **适配**：用`MediaQuery`/`LayoutBuilder`/`Expanded`/`Flexible`适配不同屏幕，禁止硬编码像素值（硬编码=小屏溢出大屏留白）
7. **Key使用**：列表中动态项必须用`ValueKey`/`ObjectKey`，禁止用index做key（index做key=增删项时状态错乱，输入框内容跳到错误行）
8. **const优化**：不变的Widget用`const`构造，减少不必要的rebuild

---

## 完整审查流程（手动 /flutter-development 或专项审查时执行）

### Phase 1: 项目结构与配置

1. **标准项目结构**：
```
lib/
├── main.dart              # 入口
├── app.dart               # MaterialApp/CupertinoApp配置
├── core/                  # 核心工具（网络/存储/常量/主题）
│   ├── network/
│   ├── storage/
│   ├── theme/
│   └── constants/
├── features/              # 功能模块（按业务划分）
│   └── feature_name/
│       ├── data/          # 数据层（Repository/DataSource/Model）
│       ├── domain/        # 领域层（Entity/UseCase）
│       └── presentation/  # 表现层（Screen/Widget/Controller）
├── shared/                # 共享组件
│   ├── widgets/
│   └── utils/
└── l10n/                  # 国际化
```

2. **pubspec.yaml规范**：
   - 依赖版本锁定（`^x.y.z`），禁止`any`或无版本约束（无约束=下次pub get可能拉到不兼容版本导致编译失败）
   - `flutter_lints`或`very_good_analysis`启用严格lint
   - 按字母顺序排列dependencies
   - `dev_dependencies`与`dependencies`分开

3. **Dart语言规范**：
   - 启用`null safety`（Dart 3.x默认）
   - 用`final`声明不变变量，禁止全用`var`（var丢失不可变意图，增加误修改风险）
   - 模式匹配（Dart 3.0+）：`switch`表达式、`if-case`、解构
   - `sealed class`替代枚举+联合类型（Dart 3.0+）
   - `extension type`替代typedef（Dart 3.3+）
   - Records`(int, String)`替代临时类/Tuple

### Phase 2: Widget与UI开发

4. **Widget选择速查**：

| 需求 | Widget | 注意事项 |
|------|--------|----------|
| 滚动列表 | `ListView.builder` | 大量数据必须用builder懒加载，❌禁止`ListView(children:[...])`加载1000+项 |
| 网格 | `GridView.builder` | 同上，必须builder |
| 可滚动嵌套 | `CustomScrollView`+`SliverList`/`SliverGrid` | 禁止ListView嵌套ListView（嵌套滚动=手势冲突+性能灾难） |
| 自适应布局 | `LayoutBuilder`/`MediaQuery` | 响应式布局，非硬编码 |
| 平台自适应 | `Platform.isIOS`/`.isAndroid` | 或用`defaultTargetPlatform` |
| 底部Sheet | `showModalBottomSheet` | 大内容用`DraggableScrollableSheet` |
| 输入框 | `TextField`+`TextEditingController` | dispose中释放controller |
| 图片 | `Image.network`/`CachedNetworkImage` | 网络图片必须有placeholder+error+缓存 |

5. **主题与样式**：
   - 用`Theme.of(context)`获取主题色，禁止硬编码`Color(0xFF...)`（硬编码=换主题时散落各处无法统一修改）
   - Material 3：`useMaterial3: true`+`ColorScheme.fromSeed()`
   - 文字样式用`Theme.of(context).textTheme.bodyMedium`
   - 间距用常量定义（`const gap4 = 4.0`等）

6. **动画**：
   - 简单动画：`AnimatedContainer`/`AnimatedOpacity`/`AnimatedSwitcher`（隐式动画）
   - 复杂动画：`AnimationController`+`Tween`（显式动画，dispose中释放controller）
   - Hero动画：`Hero(tag:)`跨页面元素过渡
   - 禁止在build()中创建AnimationController（每次rebuild创建新controller=内存泄漏+动画混乱）

### Phase 3: 状态管理

7. **推荐方案对比**：

| 方案 | 适用场景 | 优势 | 注意 |
|------|----------|------|------|
| Riverpod | 中大型项目 | 编译时安全/无context依赖/可测试 | 学习曲线较陡 |
| Bloc/Cubit | 大型项目/团队开发 | 事件驱动/状态可预测/可追踪 | 样板代码较多 |
| Provider | 小中型项目 | 简单/Flutter官方推荐 | 大项目context依赖问题 |
| GetX | ❌不推荐 | — | 全局状态难追踪/魔法太多/测试困难 |

8. **状态管理铁律**：
   - 状态分层：UI状态(loading/error/success) vs 业务状态(用户数据/配置) vs 临时状态(表单输入)
   - UI状态用Widget本地State，业务状态用全局方案
   - 禁止在Widget中直接调HTTP/数据库（Widget只负责展示，数据操作在Repository层）
   - 状态不可变(immutable)：用`copyWith()`产生新状态，禁止直接修改状态对象属性（直接修改=框架检测不到变化，UI不刷新）

### Phase 4: 平台通道与原生交互

9. **平台通道选型**（优先级从高到低）：

| 方案 | 适用场景 | 类型安全 | 性能 |
|------|----------|----------|------|
| **Pigeon** | 结构化API调用 | ✅代码生成 | 中 |
| **dart:ffi** | C/C++库调用 | ✅编译时 | 高 |
| **FFIgen/JNIgen** | 自动绑定原生API | ✅代码生成 | 高 |
| **MethodChannel** | 简单一次性调用 | ❌字符串key | 中 |
| **EventChannel** | 持续数据流(传感器/BLE) | ❌字符串key | 中 |
| **BasicMessageChannel** | 自定义编解码 | ❌手动 | 低 |

10. **Pigeon使用**（推荐方式）：
```dart
// 定义接口（pigeons/messages.dart）
@ConfigurePigeon(PigeonOptions(
  dartOut: 'lib/src/messages.g.dart',
  kotlinOut: 'android/app/src/.../Messages.g.kt',
  swiftOut: 'ios/Runner/Messages.g.swift',
))
@HostApi()  // Dart调原生
abstract class DeviceApi {
  @async
  String getDeviceInfo();
  @async
  bool connectDevice(String deviceId);
}

@FlutterApi()  // 原生调Dart
abstract class DeviceCallbackApi {
  void onDeviceDisconnected(String deviceId);
  void onDataReceived(Uint8List data);
}
```

11. **MethodChannel手写（仅简单场景）**：
```dart
// ❌ 错误：硬编码字符串，无类型安全
final result = await channel.invokeMethod('getDevice', {'id': '123'});

// ✅ 正确：封装+类型安全+错误处理
class DeviceChannel {
  static const _channel = MethodChannel('com.app/device');
  
  Future<DeviceInfo> getDevice(String id) async {
    try {
      final result = await _channel.invokeMethod<Map>('getDevice', {'id': id});
      return DeviceInfo.fromMap(result!);
    } on PlatformException catch (e) {
      throw DeviceException('Failed to get device: ${e.message}');
    }
  }
}
```

### Phase 5: 硬件对接专项

12. **BLE蓝牙**（推荐库：`flutter_blue_plus`）：
    - 扫描前检查蓝牙状态+权限（Android需位置权限）
    - 连接必须有超时（默认15秒），禁止无超时连接（设备不响应=永久挂起）
    - 每次操作前检查连接状态，禁止假设已连接（BLE随时可能断连）
    - 读写特征值必须有错误处理+重试（BLE通信不可靠）
    - 断连监听+自动重连策略（指数退避，最多3次）
    - MTU协商：`requestMtu(512)` 提升吞吐量
    - Android 12+需`BLUETOOTH_SCAN`/`BLUETOOTH_CONNECT`权限

13. **传感器/硬件通用规则**：
    - 数据流必须节流（throttle/debounce），禁止每次传感器变化都setState（60Hz传感器=每秒60次rebuild=卡死）
    - 后台时暂停监听，前台时恢复（`WidgetsBindingObserver.didChangeAppLifecycleState`）
    - 硬件不可用时必须有降级方案（模拟数据/提示用户）
    - USB通信（`usb_serial`）：打开/关闭端口必须配对，异常时确保端口释放

14. **权限处理**（`permission_handler`）：
```dart
// 正确的权限请求流程
final status = await Permission.bluetooth.request();
if (status.isGranted) {
  // 执行操作
} else if (status.isPermanentlyDenied) {
  // 引导用户到设置页
  openAppSettings();
} else {
  // 显示说明为什么需要此权限
}
```

### Phase 6: 性能优化

15. **渲染性能**：
    - `const`构造器减少rebuild
    - `RepaintBoundary`隔离频繁重绘区域
    - 列表用`ListView.builder`/`ListView.separated`懒加载
    - 避免`Opacity` Widget（触发离屏渲染），用`AnimatedOpacity`或直接改颜色alpha
    - 图片缓存：`CachedNetworkImage`+`cacheWidth`/`cacheHeight`限制解码尺寸

16. **内存管理**：
    - dispose中释放所有controller/subscription/timer
    - 大图用`ResizeImage`或`cacheWidth`/`cacheHeight`
    - 避免在State中持有大集合，考虑分页加载
    - 使用DevTools Memory tab检查泄漏

17. **编译与包体积**：
    - `--split-debug-info`分离调试符号
    - `--obfuscate`代码混淆
    - `--tree-shake-icons`移除未用图标
    - 分析包体积：`flutter build apk --analyze-size`

### Phase 7: 常见坑与解决方案

18. **高频踩坑清单**：

| 坑 | 症状 | 原因 | 解决 |
|-----|------|------|------|
| setState after dispose | 崩溃 | 异步回调在页面已销毁后执行 | `if (mounted) setState(...)` |
| 键盘遮挡输入框 | 输入框被遮挡 | 未处理键盘弹出 | `Scaffold(resizeToAvoidBottomInset: true)` |
| ListView嵌套 | 报错/无法滚动 | 双滚动冲突 | 内层`shrinkWrap:true`+`NeverScrollableScrollPhysics()` |
| 图片闪烁 | 列表滚动图片闪 | 无缓存/key不稳定 | `CachedNetworkImage`+稳定key |
| 平台字体差异 | Android/iOS字体不同 | 系统默认字体不同 | 项目内嵌字体统一 |
| Hot Reload失效 | 修改不生效 | 改了main()/全局变量/原生代码 | Hot Restart或完全重启 |
| PlatformException | 原生调用崩溃 | 类型不匹配/方法未实现 | 用Pigeon替代手写Channel |
| 深层路由状态丢失 | 返回后状态重置 | 页面被dispose | 用状态管理持久化/AutomaticKeepAlive |

19. **平台差异速查**：

| 功能 | Android | iOS | 处理方式 |
|------|---------|-----|----------|
| 返回手势 | 系统返回键 | 左滑返回 | `WillPopScope`/`PopScope` |
| 权限 | 运行时+Manifest | Info.plist+运行时 | `permission_handler`统一 |
| 推送 | FCM | APNs+FCM | `firebase_messaging` |
| 文件路径 | `/data/data/pkg/` | `Documents/` | `path_provider` |
| 深链 | App Links | Universal Links | `go_router`+`app_links` |
| BLE权限 | 位置+蓝牙(12+) | 蓝牙 | 分平台请求 |

### Phase 8: 测试与发布

20. **测试策略**：
    - Widget测试：`testWidgets()`+`WidgetTester`，验证UI渲染和交互
    - 集成测试：`integration_test`包，真机/模拟器运行
    - 平台通道Mock：`TestDefaultBinaryMessengerBinding`
    - Golden测试：截图对比UI回归

21. **发布检查清单**：

| 平台 | 关键配置 | 常见问题 |
|------|----------|----------|
| Android | `minSdkVersion`/`targetSdkVersion`/签名配置/ProGuard规则 | 混淆导致反射失败 |
| iOS | Bundle ID/签名证书/Provisioning Profile/最低版本 | 证书过期/Profile不匹配 |
| 通用 | 版本号递增/Changelog/隐私政策/权限说明 | 版本号未递增被拒 |

## 约束
- 项目内只用一种状态管理方案，禁止混用（混用=状态流不可追踪，bug无法定位）
- 平台通道优先Pigeon/FFI代码生成，禁止大量手写字符串key的MethodChannel（字符串typo=运行时崩溃，编译器无法提前发现）
- 硬件通信必须有超时+错误处理+断连恢复，禁止假设连接永远可用（硬件通信天然不可靠，无容错=生产环境频繁崩溃）
- 列表/网格必须用builder懒加载，禁止一次性加载全部数据到Widget（全量加载=OOM+卡顿，100项就能感知到）
- dispose中必须释放所有controller/subscription/timer，禁止遗漏（遗漏=内存泄漏+后台持续执行+电量消耗）
- 传感器/BLE数据流必须节流，禁止每次变化都rebuild（高频rebuild=UI卡死，用户体验为零）
