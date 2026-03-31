# Sandbox 功能开发记录

## 概述

本文档记录 cc-mini sandbox（沙箱）功能的完整开发过程。该功能使 BashTool 执行的 shell 命令在 Linux bubblewrap (bwrap) 沙箱中运行，限制文件系统读写和网络访问，防止恶意或意外的破坏性操作。

---

## 一、开发背景

cc-mini 作为 Claude Code 的 Python 复刻版本，在此之前缺少沙箱隔离能力——BashTool 的所有命令都直接在宿主机执行，存在安全风险。原版 Claude Code 通过 `@anthropic-ai/sandbox-runtime` 库实现了基于 bubblewrap 的沙箱系统，我们需要在 Python 中复现同等功能。

### 参考原版文件

| 原版文件 | 对应功能 |
|---------|---------|
| `sandboxTypes.ts` (91-144) | SandboxSettings 类型定义 |
| `shouldUseSandbox.ts` (21-153) | 排除命令匹配 + 沙箱决策 |
| `sandbox-adapter.ts` (172-922) | 配置转换、依赖检查、命令包装、管理器接口 |
| `Shell.ts` (259-273) | 实际执行沙箱命令 |
| `sandbox-toggle.tsx` | /sandbox REPL 命令 |

---

## 二、设计阶段

### 2.1 方案制定

按照 `sandbox_dev/00_overview.md` 至 `08_notes.md` 共 9 个设计文档制定了完整方案：

- **模块拆分**：将沙箱功能拆为 5 个子模块（config / checker / command_matcher / wrapper / manager），各司其职
- **对外接口**：通过 `SandboxManager` 统一门面暴露，其他模块只需与 manager 交互
- **改造策略**：对现有 3 个模块（bash.py / permissions.py / main.py）做最小化改造，所有新参数均可选，保持向后兼容

### 2.2 关键设计决策

1. **TOML 而非 JSON**：cc-mini 已有 TOML 配置体系，统一格式
2. **不依赖外部沙箱库**：原版通过 npm 包 `@anthropic-ai/sandbox-runtime` 生成 bwrap 命令，我们直接在 Python 中生成
3. **类实例而非静态方法**：原版用模块级静态函数，cc-mini 使用 `SandboxManager` 类实例，方便依赖注入和测试
4. **沙箱默认关闭**：`SandboxConfig.enabled` 默认 `False`，不影响现有用户
5. **优雅降级**：bwrap 不可用时自动回退到普通执行，不报错

---

## 三、实现阶段

### 3.1 实现顺序

按依赖关系从底向上实现，共 4 个阶段：

```
阶段 1：基础模块（无外部依赖）
  ├── sandbox/__init__.py
  ├── sandbox/config.py          -- 纯数据类 + TOML 读写
  ├── sandbox/checker.py         -- 系统依赖检测
  └── sandbox/command_matcher.py -- 纯逻辑匹配

阶段 2：核心模块（依赖阶段 1）
  ├── sandbox/wrapper.py         -- bwrap 命令行生成
  └── sandbox/manager.py         -- 统一门面

阶段 3：现有模块改造
  ├── tools/bash.py              -- 注入 SandboxManager
  ├── permissions.py             -- auto-allow 联动
  └── main.py                    -- 初始化 + /sandbox 命令

阶段 4：测试编写
  ├── test_sandbox_config.py
  ├── test_sandbox_checker.py
  ├── test_sandbox_command_matcher.py
  ├── test_sandbox_wrapper.py
  ├── test_sandbox_manager.py
  └── test_sandbox_integration.py
```

### 3.2 各模块实现细节

#### sandbox/config.py

定义了两个数据类：

- `SandboxFilesystemConfig`：文件系统限制（allow_write / deny_write / deny_read / allow_read）
- `SandboxConfig`：总配置（enabled / auto_allow_bash / allow_unsandboxed / excluded_commands / filesystem / unshare_net）

实现了 TOML 读写：
- `load_sandbox_config()`：从 TOML `[sandbox]` 段加载，支持多文件优先级
- `save_sandbox_config()`：写回 TOML，仅更新 `[sandbox]` 段，保留其他内容
- 内置了一个最小 TOML 写入器（`_write_toml`），避免引入额外依赖

#### sandbox/checker.py

4 级依赖检查：
1. 平台检查（仅 Linux）
2. bwrap 二进制存在性（`shutil.which`）
3. user namespace 支持（读 `/proc/sys/kernel/unprivileged_userns_clone`）
4. bwrap 实际运行测试（`bwrap --ro-bind / / -- /bin/true`）

返回 `DependencyCheck` 数据类，`.ok` 属性反映是否可用。

#### sandbox/command_matcher.py

实现三种匹配规则：
- **exact**：无空格无通配符的模式（如 `"git"` 仅匹配 `"git"`）
- **prefix**：含空格的模式（如 `"npm run"` 匹配 `"npm run test"`）
- **wildcard**：含 `*` 或 `?` 的模式（如 `"docker *"` 匹配 `"docker build ."`）

复合命令处理：
- 按 `&&` 拆分子命令
- 对每个子命令尝试剥离环境变量前缀（`FOO=bar cmd` -> `cmd`）
- 任一子命令匹配任一规则即返回 True

#### sandbox/wrapper.py

核心模块，生成 bwrap 命令行参数。挂载策略：

```
1. --ro-bind / /           # 全局只读（安全基线）
2. --dev /dev              # 最小设备文件
3. --proc /proc            # 进程信息
4. --tmpfs /tmp            # 临时文件系统
5. --bind <allow_write>    # 开放可写目录
6. --ro-bind <deny_write>  # 强制只读（覆盖上面的可写）
7. --tmpfs <deny_read>     # 隐藏目录（用空 tmpfs 遮盖）
8. --bind <cwd> <cwd>      # 工作目录可写
9. --unshare-net           # 网络隔离（可选）
10. --die-with-parent      # 父进程退出时杀子进程
11. --unshare-pid          # PID namespace 隔离
12. --ro-bind <protected>  # 保护配置文件（最后挂载，最高优先级）
13. -- /bin/sh -c <cmd>    # 执行用户命令
```

挂载顺序很重要：bwrap 按参数顺序处理，后面的覆盖前面的。保护路径放在最后确保不可被绕过。

提供两种输出接口：
- `build_bwrap_args()` -> `list[str]`（用于 `subprocess.run(args)`）
- `wrap_command()` -> `str`（用于 `shell=True`，经过 `shlex.quote` 防注入）

#### sandbox/manager.py

统一门面层，协调其他 4 个子模块：

- `is_enabled()`：配置启用 + 依赖满足
- `is_auto_allow()`：auto-allow 模式判断
- `should_sandbox(command)`：4 层决策（启用? -> dangerously_disable? -> 空命令? -> 排除?）
- `wrap(command)`：生成沙箱命令
- `set_mode(mode)`：三种模式切换（auto-allow / regular / disabled）
- `check_dependencies()`：缓存结果，每会话只检查一次

### 3.3 现有模块改造

#### tools/bash.py

改造点：
- 构造函数新增可选 `sandbox_manager` 参数
- `execute()` 新增可选 `dangerously_disable_sandbox` 参数
- `input_schema` 新增该字段供模型调用
- 执行前调用 `should_sandbox()` 决策，如需沙箱则调用 `wrap()` 包装命令
- 其余逻辑（超时、输出格式化）完全不变

#### permissions.py

改造点：
- `__init__` 新增可选 `sandbox_manager` 参数
- `check()` 在原有 4 级判断后新增第 5 级：sandbox auto-allow
- 4 个条件全部满足才放行：工具是 Bash + manager 存在 + auto-allow 模式 + 命令会被沙箱化
- 被 exclude 的命令不走 auto-allow（因为它不在沙箱中执行）

#### main.py

改造点：
- 导入 sandbox 模块
- `main()` 中加载 sandbox 配置、创建 `SandboxManager`、注入到 `BashTool` 和 `PermissionChecker`
- REPL 循环中检测 `/sandbox` 前缀，路由到 `_handle_sandbox_command()`
- 实现 3 个辅助函数：
  - `_handle_sandbox_command()`：解析子命令（status / mode / exclude）
  - `_show_sandbox_status()`：显示当前沙箱状态和依赖检查结果
  - `_interactive_sandbox_setup()`：交互式三选一模式切换

---

## 四、问题与修复

### 4.1 Python 3.10 tomllib 兼容性

**问题**：开发环境为 Python 3.10，而 `tomllib` 是 Python 3.11 才加入标准库的模块。`sandbox/config.py` 的 `import tomllib` 直接报错。

**发现过程**：首次运行 `from core.sandbox import SandboxManager` 时触发 `ModuleNotFoundError`。

**解决**：添加兼容性导入，回退到第三方 `tomli` 包：

```python
import sys
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
```

**额外发现**：现有的 `core/config.py` 也有同样的问题（`import tomllib` 硬编码），一并修复。

### 4.2 load_sandbox_config 的循环导入

**问题**：`sandbox/config.py` 中 `load_sandbox_config()` 通过 `from ..config import _DEFAULT_CONFIG_PATHS` 导入默认配置路径，这会触发 `core/config.py` 的加载（包括其 `import tomllib`），在 Python 3.10 上报错。

**解决**：消除对 `core.config` 的导入依赖，在 `load_sandbox_config()` 内直接构造默认路径：

```python
if not config_paths:
    config_paths = (
        Path.home() / ".config" / "cc-mini" / "config.toml",
        Path.cwd() / ".cc-mini.toml",
    )
```

### 4.3 wrapper 测试断言逻辑错误

**问题**：`test_deny_write_paths` 中通过 `args.index(str(deny_dir))` 找到路径后，假设 `args[idx-1]` 是路径、`args[idx-2]` 是 `--ro-bind`。但由于 `--ro-bind` 有两个参数（src 和 dest），`index()` 可能找到的是 src 位置而非 dest。

**解决**：改为遍历三元组查找 `--ro-bind <path> <path>` 模式：

```python
for i in range(len(args) - 2):
    if args[i] == "--ro-bind" and args[i+1] == deny_str and args[i+2] == deny_str:
        found = True
        break
```

---

## 五、测试结果

### 5.1 测试矩阵

| 测试文件 | 测试数量 | 状态 |
|---------|---------|------|
| test_sandbox_config.py | 12 | 全部通过 |
| test_sandbox_checker.py | 8 | 全部通过 |
| test_sandbox_command_matcher.py | 20 | 全部通过 |
| test_sandbox_wrapper.py | 16 | 全部通过 |
| test_sandbox_manager.py | 16 | 全部通过 |
| test_sandbox_integration.py | 9 | 跳过（需 bwrap） |
| 原有测试 | 52 | 全部通过（无回归） |
| **合计** | **133** | **全部通过** |

### 5.2 集成测试覆盖场景

集成测试需要 bwrap 可用的 Linux 环境，覆盖以下场景：

- 沙箱内写 `/` 失败（只读保护）
- 沙箱内 cwd 可写
- 命令输出正确返回
- `/etc/passwd` 只读可读
- 管道命令正常工作
- 网络隔离生效
- 超时机制正常
- `.cc-mini.toml` 配置文件被保护
- SandboxManager.wrap() 端到端执行

---

## 六、文件清单

### 新增文件

| 文件 | 行数 | 用途 |
|------|------|------|
| `src/core/sandbox/__init__.py` | 22 | 包导出 |
| `src/core/sandbox/config.py` | 154 | 配置数据类 + TOML 读写 |
| `src/core/sandbox/checker.py` | 76 | 依赖检查 |
| `src/core/sandbox/command_matcher.py` | 93 | 排除命令匹配 |
| `src/core/sandbox/wrapper.py` | 115 | bwrap 命令生成 |
| `src/core/sandbox/manager.py` | 107 | 统一管理器 |
| `tests/test_sandbox_config.py` | 112 | 配置测试 |
| `tests/test_sandbox_checker.py` | 94 | 检查器测试 |
| `tests/test_sandbox_command_matcher.py` | 113 | 匹配器测试 |
| `tests/test_sandbox_wrapper.py` | 119 | 包装器测试 |
| `tests/test_sandbox_manager.py` | 131 | 管理器测试 |
| `tests/test_sandbox_integration.py` | 95 | 集成测试 |

### 改造文件

| 文件 | 改动概述 |
|------|---------|
| `src/core/tools/bash.py` | +SandboxManager 注入, +dangerously_disable_sandbox 参数 |
| `src/core/permissions.py` | +sandbox auto-allow 判断分支 |
| `src/core/main.py` | +sandbox 初始化, +/sandbox REPL 命令 |
| `src/core/config.py` | 修复 Python 3.10 tomllib 兼容性 |

---

## 七、与原版的对齐情况

| 原版功能 | 实现状态 | 说明 |
|---------|---------|------|
| 三种模式（auto-allow / regular / disabled） | 已实现 | 通过 `SandboxManager.set_mode()` |
| excludedCommands 三种匹配 | 已实现 | prefix / exact / wildcard |
| dangerouslyDisableSandbox | 已实现 | BashTool 参数 + config.allow_unsandboxed |
| 依赖检查 | 已实现 | 平台 + bwrap + userns + 运行测试 |
| 文件系统读写限制 | 已实现 | bwrap bind mount 策略 |
| 网络隔离 | 已实现 | bwrap --unshare-net |
| /sandbox 命令 | 已实现 | status / mode / exclude / 交互式 |
| settings 文件保护 | 已实现 | .cc-mini.toml + CLAUDE.md 只读保护 |
| 配置持久化 | 已实现 | TOML [sandbox] 段 |
| macOS sandbox-exec | 未实现 | 设计文档明确为非目标 |
| 多层设置源 | 未实现 | 简化为两层（项目本地 + 用户全局） |
| bare-repo 攻击防护 | 未实现 | 后续迭代 |
