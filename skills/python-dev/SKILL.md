---
name: python-dev
description: Python开发技能 - Python 3.12+语法、类型注解、async/await异步编程、Pydantic数据校验、FastAPI/Django Web框架、SQLAlchemy ORM、pytest测试、虚拟环境、包管理、性能优化。当你涉及Python代码开发、异步编程、类型注解、数据校验、Web API、ORM、自动化脚本时必须使用此技能。即使用户只是说"写个Python脚本"或"改下Python代码"，也应触发。
---

# Python开发技能 (Python Development Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[Python核心清单]** ① 类型注解必写：函数参数+返回值+类属性，用`mypy --strict`检查 ② async路由禁止阻塞调用（time.sleep/requests/同步DB），用await或run_in_threadpool ③ 数据校验用Pydantic，禁止手写if-else校验
> **[三禁]** ❌`except Exception: pass`（吞掉所有错误） ❌全局可变状态（模块级list/dict被多请求共享） ❌`from module import *`（命名空间污染）
> **[命名铁律]** 函数/变量snake_case，类PascalCase，常量UPPER_SNAKE，私有属性_前缀，包名小写无下划线

写/改Python代码时，强制遵守：
1. **类型注解**：所有函数签名必须有类型注解，复杂类型用`typing`或内置泛型(`list[str]`, `dict[str, int]`, `T | None`)
2. **异步纪律**：async函数内禁止同步阻塞调用，同步SDK用`asyncio.to_thread()`或`run_in_threadpool()`包装
3. **数据校验**：API入参用Pydantic BaseModel，禁止手写dict取值+if校验
4. **错误处理**：except指定具体异常类型，附加上下文信息，禁止裸except或pass
5. **资源管理**：文件/连接/锁用`with`/`async with`上下文管理器，禁止手动open+close
6. **不可变优先**：用tuple替代list（不需要修改时），用frozenset替代set，用`@dataclass(frozen=True)`
7. **路径操作**：用`pathlib.Path`替代`os.path`，用`f-string`替代`%`/`.format()`

---

## 类型注解

### 现代类型语法（Python 3.10+）
```python
# ✅ 内置泛型（不需要typing导入）
def process(items: list[str]) -> dict[str, int]:
    return {item: len(item) for item in items}

# ✅ 联合类型用 | 替代 Union
def get_user(id: int) -> User | None:
    ...

# ✅ TypeVar + Generic
from typing import TypeVar, Generic
T = TypeVar('T')

class Repository(Generic[T]):
    async def get(self, id: int) -> T | None: ...
    async def save(self, item: T) -> T: ...

# ✅ TypeGuard 类型守卫
from typing import TypeGuard
def is_string_list(val: list[object]) -> TypeGuard[list[str]]:
    return all(isinstance(x, str) for x in val)
```

### 常用类型速查
| 类型 | 用途 | 示例 |
|------|------|------|
| `list[T]` | 可变序列 | `list[int]` |
| `tuple[T, ...]` | 不可变序列 | `tuple[str, ...]` |
| `dict[K, V]` | 映射 | `dict[str, Any]` |
| `set[T]` | 可变集合 | `set[int]` |
| `T \| None` | 可选值 | `str \| None` |
| `Callable[[P], R]` | 可调用 | `Callable[[int], str]` |
| `Sequence[T]` | 只读序列（参数用） | `Sequence[str]` |
| `Mapping[K, V]` | 只读映射（参数用） | `Mapping[str, int]` |
| `Literal["a", "b"]` | 字面量类型 | `Literal["asc", "desc"]` |

## 异步编程

### async/await基础
```python
import asyncio

# ✅ 并行请求
async def fetch_all(urls: list[str]) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_one(session, url) for url in urls]
        return await asyncio.gather(*tasks)

# ✅ 超时控制
async def fetch_with_timeout(url: str, timeout: float = 10) -> bytes:
    async with asyncio.timeout(timeout):  # Python 3.11+
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                return await resp.read()

# ✅ 信号量限制并发
sem = asyncio.Semaphore(10)
async def limited_fetch(url: str) -> bytes:
    async with sem:
        return await fetch(url)
```

### async陷阱
| 陷阱 | 示例 | 正确做法 |
|------|------|----------|
| async中用同步阻塞 | `async def f(): time.sleep(1)` | `await asyncio.sleep(1)` |
| async中用requests | `async def f(): requests.get(url)` | `await session.get(url)` (aiohttp) |
| 忘记await | `result = async_func()` → 协程对象 | `result = await async_func()` |
| gather无错误处理 | `await asyncio.gather(*tasks)` 一个失败全部失败 | `return_exceptions=True` |
| sync路由中调async | 事件循环已运行报错 | `asyncio.run()`或在sync框架中用threadpool |

### 同步SDK包装
```python
# FastAPI中使用同步SDK
from fastapi.concurrency import run_in_threadpool

@app.get("/sync-operation")
async def call_sync_sdk():
    result = await run_in_threadpool(sync_client.make_request, data=payload)
    return result

# 通用方式
result = await asyncio.to_thread(blocking_func, arg1, arg2)
```

## Pydantic数据校验

### 基础模型
```python
from pydantic import BaseModel, Field, EmailStr, field_validator

class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    age: int = Field(ge=0, le=150)
    role: Literal["admin", "user"] = "user"

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("名称不能为空白")
        return v.strip()

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    model_config = ConfigDict(from_attributes=True)  # 支持ORM对象→Model
```

### Pydantic最佳实践
| 实践 | 说明 |
|------|------|
| **自定义BaseModel** | 统一datetime序列化/公共方法 |
| **Settings拆分** | 按模块拆BaseSettings（AuthConfig/DBConfig/AppConfig） |
| **schema复用** | Create/Update/Response分开定义，用继承减少重复 |
| **`model_config`** | `from_attributes=True`支持ORM，`str_strip_whitespace=True`自动去空格 |
| **`Field`约束** | 用Field的min_length/ge/pattern替代自定义validator |

### Settings配置管理
```python
from pydantic_settings import BaseSettings

class DBConfig(BaseSettings):
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str
    DB_PASS: str
    DB_NAME: str

    @property
    def dsn(self) -> str:
        return f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    model_config = SettingsConfigDict(env_file=".env")

db_config = DBConfig()
```

## FastAPI模式

### 项目结构（按功能域分包）
```
src/
├── auth/
│   ├── router.py       # 路由端点
│   ├── schemas.py      # Pydantic模型
│   ├── models.py       # DB模型
│   ├── service.py      # 业务逻辑
│   ├── dependencies.py # 依赖注入
│   └── exceptions.py   # 模块异常
├── posts/
│   ├── router.py
│   ├── schemas.py
│   └── ...
├── config.py           # 全局配置
├── database.py         # DB连接
└── main.py             # 入口
```

### 依赖注入（超越DI：用于数据校验）
```python
# dependencies.py
async def valid_post_id(post_id: UUID4) -> dict:
    post = await service.get_by_id(post_id)
    if not post:
        raise PostNotFound()
    return post

# 链式依赖
async def valid_owned_post(
    post: dict = Depends(valid_post_id),
    user: dict = Depends(get_current_user),
) -> dict:
    if post["creator_id"] != user["id"]:
        raise ForbiddenError()
    return post

# router.py - 所有使用post_id的路由共享校验逻辑
@router.get("/posts/{post_id}")
async def get_post(post: dict = Depends(valid_post_id)):
    return post
```

### async路由 vs sync路由
```python
# ✅ I/O密集型：用async + await
@router.get("/users/{id}")
async def get_user(id: int, db: AsyncSession = Depends(get_db)):
    return await db.get(User, id)

# ✅ CPU密集型或同步SDK：用sync（FastAPI自动放入threadpool）
@router.get("/cpu-heavy")
def compute_heavy():
    return heavy_computation()  # 不阻塞事件循环

# ❌ async中调同步阻塞（阻塞整个事件循环！）
@router.get("/terrible")
async def terrible():
    time.sleep(10)  # 所有请求都会被阻塞
```

## 错误处理

```python
# ✅ 自定义异常层次
class AppError(Exception):
    def __init__(self, message: str, code: int = 500):
        self.message = message
        self.code = code

class NotFoundError(AppError):
    def __init__(self, resource: str, id: Any):
        super().__init__(f"{resource} {id} 不存在", code=404)

class ValidationError(AppError):
    def __init__(self, detail: str):
        super().__init__(detail, code=400)

# ✅ FastAPI全局异常处理
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.code, content={"code": exc.code, "msg": exc.message})

# ✅ 具体异常+上下文
try:
    user = await db.get(User, user_id)
except SQLAlchemyError as e:
    logger.error("查询用户失败 user_id=%s: %s", user_id, e)
    raise AppError("数据库查询失败") from e

# ❌ 裸except
try: ...
except: pass  # 吞掉所有错误，包括KeyboardInterrupt
```

## 测试（pytest）

```python
import pytest
from httpx import AsyncClient, ASGITransport

# ✅ 异步测试客户端（从第一天就用async）
@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client

@pytest.mark.asyncio
async def test_create_user(client: AsyncClient):
    resp = await client.post("/users", json={"name": "test", "email": "a@b.com"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test"

# ✅ Mock外部依赖
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get.return_value = {"id": 1, "name": "Test"}
    return db

# ✅ 参数化测试
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("", ""),
    ("123", "123"),
])
def test_upper(input: str, expected: str):
    assert input.upper() == expected
```

## 性能优化

| 场景 | 优化方案 |
|------|----------|
| I/O密集 | `asyncio.gather()`并行，`Semaphore`限并发 |
| CPU密集 | `ProcessPoolExecutor`或`multiprocessing` |
| 大数据遍历 | 生成器/`yield`替代list，`itertools`工具 |
| JSON序列化 | `orjson`替代标准`json`（快5-10x） |
| 字符串拼接 | `"".join(list)`替代`+=`循环 |
| 缓存 | `@functools.lru_cache`/`@functools.cache` |
| DB查询 | `select_related`/`prefetch_related`（Django），`joinedload`（SQLAlchemy） |
| 启动速度 | 延迟导入重模块（`import`放在函数内） |

## 包管理与工具链

```bash
# uv（推荐，极快的Python包管理器）
uv venv                    # 创建虚拟环境
uv pip install package     # 安装依赖
uv pip compile requirements.in  # 锁定依赖

# poetry
poetry init                # 初始化项目
poetry add package         # 添加依赖
poetry install             # 安装所有依赖

# 代码质量
ruff check .               # lint（替代flake8+isort+pyupgrade）
ruff format .              # 格式化（替代black）
mypy --strict src/         # 类型检查
pytest -xvs --cov=src      # 测试+覆盖率
```

### pyproject.toml模板
```toml
[project]
name = "my-project"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
line-length = 88
select = ["E", "F", "I", "N", "W", "UP"]

[tool.mypy]
strict = true
warn_return_any = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## 常见陷阱

| 陷阱 | 说明 | 正确做法 |
|------|------|----------|
| 可变默认参数 | `def f(x=[]):` 所有调用共享同一个list | `def f(x: list \| None = None):` |
| 闭包变量 | `for i in range(5): lambdas.append(lambda: i)` 全是4 | `lambda i=i: i` |
| 浮点精度 | `0.1 + 0.2 != 0.3` | `Decimal`或整数分表示金额 |
| `is` vs `==` | `is`比较身份，`==`比较值 | 只对None/True/False用`is` |
| GIL限制 | 多线程不能并行CPU密集任务 | CPU密集用`multiprocessing` |
| 循环导入 | A imports B, B imports A | 延迟导入或重构结构 |
| `datetime.now()` | 无时区信息 | `datetime.now(tz=ZoneInfo("Asia/Shanghai"))` |

## 防Bug铁律（写Python代码时强制执行）

### 写前三查（每次写函数前必须完成）
1. **查输入**：参数可能是None/空字符串/空列表/负数/超大值吗？→加守卫
2. **查边界**：索引越界？字典KeyError？文件不存在？→防御性编程
3. **查副作用**：函数修改了传入的list/dict吗？→用copy或返回新对象

### 高频Bug速查表
| Bug类型 | 典型代码 | 修复 |
|---------|---------|------|
| None访问 | `user.name`但user可能None | `if user is not None:` 或 `user.name if user else ""` |
| 空集合 | `items[0]`但items可能空 | `items[0] if items else default` |
| 字符串编码 | `open(f)`默认编码因系统而异 | `open(f, encoding="utf-8")` |
| 整数除法 | `a / b`除零 | `a / b if b != 0 else 0` |
| Dict缺key | `d["key"]`抛KeyError | `d.get("key", default)` |
| 列表修改 | 遍历中修改列表 | 用列表推导创建新列表 |
| 路径拼接 | `dir + "/" + file` | `Path(dir) / file` |
| 进程/文件未关闭 | `f = open(...)` 忘close | `with open(...) as f:` |
| subprocess挂死 | `subprocess.run()`无超时 | `timeout=30` + `check=True` |

### 脚本防呆规则（写自动化/一次性脚本时强制）
```python
# ✅ 脚本模板 — 防止反复修改
import sys
import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def main() -> int:
    parser = argparse.ArgumentParser(description="脚本功能说明")
    parser.add_argument("input", type=Path, help="输入文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    args = parser.parse_args()

    # 1. 前置检查（先验证再执行，不边做边查）
    if not args.input.exists():
        logger.error("文件不存在: %s", args.input)
        return 1

    # 2. 核心逻辑
    try:
        result = process(args.input, dry_run=args.dry_run)
        logger.info("完成: %s", result)
        return 0
    except Exception as e:
        logger.error("执行失败: %s", e, exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

### 脚本编写纪律
- **先验后做**：所有输入（文件/目录/URL/参数）在执行前全部验证
- **dry-run**：破坏性操作（删除/修改/发送）必须支持`--dry-run`预览
- **幂等设计**：脚本可重复执行不会产生副作用（检查已存在则跳过）
- **超时保护**：subprocess/网络请求必须设timeout，禁止无限等待
- **日志完整**：关键步骤打INFO日志，错误打ERROR+exc_info=True（完整traceback）
- **退出码**：成功return 0，失败return 1，供外部脚本判断
- ❌ 禁止硬编码路径/IP/密码/端口（用argparse或环境变量）
- ❌ 禁止`os.system()`（用subprocess.run + check=True + timeout）

## 代码卫生（防止屎山）

### 函数设计
- **单一职责**：一个函数只做一件事，名字能说清它做什么
- **长度限制**：函数体≤50行，超过就拆分。文件≤400行，超过就分模块
- **参数限制**：参数≤5个，超过用dataclass/TypedDict封装
- **返回值明确**：禁止一个函数有时返回dict有时返回list有时返回None
- **禁止深嵌套**：if嵌套≤3层，超过用early return/guard clause扁平化

### 清理纪律
- **临时文件**：用`tempfile.NamedTemporaryFile(delete=True)`或`with`自动清理
- **测试文件**：测试放`tests/`目录，命名`test_*.py`，禁止散落在源码目录
- **调试代码**：`print()`/`breakpoint()`/`pdb`用完必须删除，禁止提交
- **无用导入**：ruff自动检测，CI中强制`ruff check --select F401`
- **注释掉的代码**：直接删除，需要时从git找回，禁止注释代码块残留

### 依赖卫生
- **固定版本**：`requirements.txt`中用`==`固定版本，不用`>=`
- **最小依赖**：能用标准库解决的不引第三方包
- **定期审计**：`pip audit`检查已知漏洞

## 约束
- 所有发现必须有file:line引用
- 遵循PEP 8/PEP 484/PEP 585规范
- 优先标准库，不引入不必要的第三方依赖
- 用ruff替代flake8+black+isort（统一工具链）
- 保持项目现有的代码风格，不引入新范式
- ❌ 禁止生成测试文件到非tests/目录
- ❌ 禁止保留print调试语句
- ❌ 禁止硬编码路径/密码/端口
