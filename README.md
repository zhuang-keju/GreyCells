
# Coding Agent

这是一个基于 Dify Workflow 设计的硬编码 Coding Agent。它通过多 Agent 协作循环（PM -> Coder -> Tester -> Executor -> Debugger）来自动生成高质量的 Python 代码。

## 核心特性

*   **多 Agent 协作**：完全复刻了 `CodingAgent.yml` 中的产品经理、程序员、测试工程师、Debug 专家流程。
*   Property Based Testing + debug agent arbitrator
*   Markdown extractor
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



# 🚀 Key Upgrades & Architecture Evolution
1. The "Sidecar" Protocol (核心通信协议升级)
From JSON to Markdown: 彻底摒弃了将代码包裹在 JSON 字符串中的旧模式，解决了转义符灾难（Escaping Hell）和多行字符串兼容性问题。

Structured Output: 建立了统一的三段式输出标准：

## Reasoning: 自然语言思维链（CoT）。

## Content: 纯净的代码块（Python Code）。

## Metadata: 结构化的元数据（JSON）。

2. Agent Capabilities (智能体能力增强)
QA Agent (Testcase):

Property-Based Testing (PBT): 引入 80% 随机属性测试 + 20% Happy Path 的测试策略，而非简单的硬编码断言。

Debuggability Mandate: 强制要求断言失败时打印输入参数（e.g., f"Failed on input: {x}"），为 Debug Agent 提供关键线索。

Anti-Hallucination: Prompt 使用“通用逻辑示例”而非“业务相关示例”，防止模型对 Few-Shot 样本过拟合。

Arbiter Agent (Debugger):

Parallel Fix Strategy: 支持 SOURCE, TEST, BOTH 三种修复模式，可同时修改源码和测试以解决 API 契约不匹配问题。

Truth Hierarchy: 确立了 User Story > Logic > Test 的仲裁优先级，防止为了通过测试而修改正确的需求逻辑。

3. Robust Infrastructure (鲁棒性基础设施)
AST-Based Parsing: 引入 markdown-it-py 替代正则表达式，基于抽象语法树（AST）精准提取代码块，无视缩进和换行干扰。

Schema-Driven Extraction: 实现了通用的解析器，通过传入 Schema 配置（如 Target_file vs Content）即可适配所有 Agent。

Fault Tolerance (Anti-Fragile):

Wrapper Peeling: 自动识别并剥离 LLM 多此一举添加的 ```markdown 外壳。

Auto-Completion: 自动修复未闭合的代码块（Unclosed Fences）和丢失的 Metadata。

Greedy Header Match: 模糊匹配标题逻辑，能够处理 ## Target: SOURCE 这种同行内容提取。

### 3. 查看输出

程序运行结束后，最终生成的代码将保存在 `output/` 目录下：

*   `output/main.py`: 最终的业务代码
*   `output/test_generated.py`: 最终通过的测试用例
