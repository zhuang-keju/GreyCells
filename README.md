
# Coding Agent (YAML Implementation)

这是一个基于 Dify Workflow 设计的硬编码 Coding Agent。它通过多 Agent 协作循环（PM -> Coder -> Tester -> Executor -> Debugger）来自动生成高质量的 Python 代码。

## 核心特性

*   **多 Agent 协作**：完全复刻了 `CodingAgent.yml` 中的产品经理、程序员、测试工程师、Debug 专家流程。
*   **自修正循环**：包含真实的 Python 代码执行环境，如果测试失败，Debug Agent 会自动分析错误并修正源码或测试用例。
*   **零外部依赖**：仅使用 Python 标准库（`urllib`, `subprocess`, `json`, `re` 等），无需 `pip install` 任何第三方包。
*   **真实 LLM 调用**：直接通过 REST API 调用 Google Gemini 模型。

## 环境要求

*   Python 3.10+
*   Google Gemini API Key

## 快速开始

### 1. 设置环境变量

你需要设置以下环境变量来配置 LLM。

**macOS / Linux:**
```bash
export LLM_API_KEY="your_google_gemini_api_key"
export LLM_MODEL="gemini-2.0-flash"  # 可选，默认使用 gemini-2.0-flash
```

**Windows (PowerShell):**
```powershell
$env:LLM_API_KEY="your_google_gemini_api_key"
$env:LLM_MODEL="gemini-2.0-flash"
```

### 2. 运行程序

直接运行 `coding_agent.py` 并传入你的自然语言需求：

```bash
python coding_agent.py "写一个贪吃蛇游戏，使用命令行界面，WASD控制"
```

### 3. 查看输出

程序运行结束后，最终生成的代码将保存在 `output/` 目录下：

*   `output/main.py`: 最终的业务代码
*   `output/test_generated.py`: 最终通过的测试用例

---

## 常见问题排查

**Q: `urllib.error.HTTPError: HTTP Error 400: Bad Request`**
*   **原因**: 通常是 API Key 无效，或者 Model 名称填写错误。
*   **解决**: 检查 `LLM_API_KEY` 是否正确，确保该 Key 有权限访问 `LLM_MODEL` 指定的模型。

**Q: 程序在 "Executing & Testing..." 卡住很久**
*   **原因**: 生成的代码可能包含死循环。
*   **解决**: 代码执行器内置了 30 秒超时机制。如果超过 30 秒，程序会自动判定为失败并进入 Debug 流程。

**Q: Debug Agent 没有修复代码**
*   **原因**: 可能是 LLM 输出格式不符合 JSON 要求，或者逻辑过于复杂超出模型能力。
*   **解决**: 尝试更换更强的模型（如 `gemini-1.5-pro`），或者简化需求描述。
