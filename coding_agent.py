
import os
import sys
import json
import re
import subprocess
import time
import argparse
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, Optional



from e2b_code_interpreter import Sandbox
from dotenv import load_dotenv



# ============================================================================
# Configuration & Constants
# ============================================================================

load_dotenv()

DEFAULT_MODEL = "gemini-2.0-flash"  # Default if env var not set
API_KEY_ENV = "LLM_API_KEY"
PROVIDER_ENV = "LLM_PROVIDER"
MODEL_ENV = "LLM_MODEL"
E2B_API_KEY = os.environ.get("E2B_API_KEY")

if not E2B_API_KEY:
    print("❌ 警告：未找到 E2B_API_KEY")
else:
    print("✅ E2B Key 已加载")


# ============================================================================
# LLM Client (Gemini REST API)
# ============================================================================

def call_llm(system_prompt: str, user_prompt: str) -> str:
    """
    Calls Google Gemini API using standard library urllib (Zero dependencies).
    Merges system prompt into user prompt as per instructions.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(f"Error: Environment variable {API_KEY_ENV} not set.")
        sys.exit(1)

    model = os.environ.get(MODEL_ENV, DEFAULT_MODEL)
    
    # Gemini API Endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    # Merge prompts (Gemini REST API doesn't strictly distinguish system prompt in the simplest payload)
    # We'll use the pattern: System Instruction \n\n User Instruction
    full_prompt = f"{system_prompt}\n\n{user_prompt}"

    payload = {
        "contents": [{
            "parts": [{"text": full_prompt}]
        }],
        "generationConfig": {
            "temperature": 0.7
        }
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})

    try:
        with urllib.request.urlopen(req) as response:
            result_json = json.loads(response.read().decode('utf-8'))
            
            # Parse Gemini Response
            try:
                candidates = result_json.get('candidates', [])
                if not candidates:
                    raise ValueError("No candidates returned")
                
                content = candidates[0].get('content', {})
                parts = content.get('parts', [])
                if not parts:
                    raise ValueError("No parts returned")
                
                return parts[0].get('text', "")
            except Exception as e:
                return f"Error parsing response: {str(e)} | Raw: {json.dumps(result_json)}"

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"\n[LLM Error] HTTP {e.code}: {error_body}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[LLM Error] {str(e)}")
        sys.exit(1)

# ============================================================================
# Helper Functions (Cleaners & logic from YAML)
# ============================================================================

def safe_json_decode(raw_str):
    try:
        return json.loads(f'"{raw_str}"')
    except:
        return raw_str

def remove_trailing_slash(content):
    content = content.strip()
    while content.endswith("/n") or content.endswith("\\n"):
        if content.endswith("/n"):
            content = content[:-2]
        elif content.endswith("\\n"):
            content = content[:-2]
    return content.strip()

def extract_markdown_code(text: str) -> str:
    """Extract code from markdown code blocks."""
    text = text.strip()
    pattern = r"```(?:python|json)?\s*(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text

def cleaner_source_code(llm_output: str) -> str:
    """Logic from 'Code Cleaner (1)' node."""
    code = extract_markdown_code(llm_output)
    
    # Try to unwrap if it's a JSON wrapper (sometimes LLMs wrap code in JSON)
    try:
        data = json.loads(code)
        if isinstance(data, dict) and "code" in data:
            return data["code"]
    except json.JSONDecodeError:
        pass
        
    return code.strip("`")

def cleaner_test_case(llm_output: str) -> str:
    """Logic from 'Code Cleaner (2)' node."""
    code = extract_markdown_code(llm_output)
    
    # Logic to parse the specific JSON format output by Testcase Agent
    # It expects a list of dicts with 'suffix', 'content'
    extracted_code = ""
    
    try:
        # Try pure JSON load first
        data = json.loads(code)
    except:
        # If strict JSON fails, try the regex decode fallback from YAML
        # (Simplified here to just look for content pattern as the regex in YAML was complex)
        # However, for robustness, let's assume the LLM follows instructions reasonably well
        # or that we can fix simple JSON errors.
        print("  [Cleaner] Warning: JSON decode failed for testcase, trying simple extraction...")
        # Fallback: simple text extraction if it looks like code
        if "class " in code and "unittest" in code:
            return code
        return "# Error parsing test case JSON"

    if isinstance(data, dict):
        data = [data]
        
    if isinstance(data, list):
        for obj in data:
            if obj.get("suffix") == "py":
                obj_code = obj.get("content", "")
                obj_code = remove_trailing_slash(obj_code)
                extracted_code += obj_code + "\n\n"
    
    return extracted_code.strip()

def cleaner_debug_agent(llm_output: str) -> Dict[str, str]:
    """Logic from 'Code Cleaner' (Node 1766771232701) handling Debugger output."""
    code = extract_markdown_code(llm_output)
    
    try:
        update = json.loads(code)
    except:
        # Regex fallback from YAML
        pair_pattern = (
            r'"target_file"\s*:\s*"(.*?)(?<!\\)"'
            r'.*?'
            r'"file_content"\s*:\s*"(.*?)(?<!\\)"'
        )
        match = re.search(pair_pattern, code, re.DOTALL)
        if match:
            raw_target = match.group(1)
            raw_content = match.group(2)
            return {
                "target_file": safe_json_decode(raw_target),
                "file_content": remove_trailing_slash(safe_json_decode(raw_content))
            }
        return {"target_file": "ERROR", "file_content": ""}

    return {
        "target_file": update.get("target_file"),
        "file_content": update.get("file_content")
    }

# ============================================================================
# Tool: Code Execution (Subprocess)
# ============================================================================

def execute_code(source_code: str, test_code: str) -> Dict[str, Any]:
    """
    Simulates the Vercel Code Runner.
    Writes code to temp files and runs unittest.
    """

    with Sandbox.create(api_key=E2B_API_KEY) as sandbox:
        print("🚀 沙箱已启动...")

        sandbox.files.write("main.py", source_code)
        print("✅ 文件 main.py 已写入沙箱")

        test_file_content = "import unittest\nfrom main import *\n\n" + test_code

        sandbox.files.write("test.py", test_file_content)
        print("✅ 文件 test.py 已写入沙箱")

        # proc = sandbox.commands.run("python test.py")

        # 3. 执行代码 (替代 subprocess 的部分)
        # 注意：timeout 参数直接在这里设置，单位是秒
        try:
            proc = sandbox.commands.run("python test.py", timeout=30)
            
            # E2B 的 proc 对象直接提供了 exit_code, stdout, stderr
            is_pass = proc.exit_code == 0
            
            return {
                "is_pass": is_pass,
                "stderr": proc.stderr,
                "stdout": proc.stdout,
                # 如果测试通过，error 为空；否则提示 Failed
                "error": "" if is_pass else "Tests failed"
            }

        except TimeoutError:
            # E2B 内部超时会抛出 TimeoutError
            return {
                "is_pass": False,
                "stderr": "Execution Timed Out (30s)",
                "stdout": "",
                "error": "Timeout"
            }
        except Exception as e:
            # 捕获执行过程中的其他错误（如沙箱内部崩溃等）
            return {
                "is_pass": False,
                "stderr": str(e),
                "stdout": "",
                "error": "Execution Error during run"
            }


def qa_judge(exec_result: Dict[str, Any]) -> Tuple[bool, str]:
    """Logic from 'QA Judge' node."""
    is_pass = exec_result.get("is_pass", False)
    error_log = ""
    
    if not is_pass:
        error_log = f"Summary: Tests Failed\n"
        error_log += f"Details:\n{exec_result.get('stderr')}\n"
        if exec_result.get('error'):
             error_log += f"Traceback:\n{exec_result.get('error')}"
             
    return is_pass, error_log

# ============================================================================
# Prompts (Hardcoded from YAML)
# ============================================================================

PROMPT_PM_SYSTEM = """# Role
你是一个**技术产品经理 (Technical Product Manager)**。
你的特长是平衡业务需求与技术约束。你负责给开发团队（Coder Agent）和测试团队（Test Agent）输出精准的需求文档。

# Task
分析用户输入 。
1.  **识别硬性约束：** 检查用户是否指定了具体的约束，如**数据结构**（如字典、列表）、**变量名**、**函数签名**或**输入输出类型**等。
2.  **填充业务空白：** 对于用户未提及的业务逻辑（如异常处理、边界情况），进行合理的补充和完善。
3.  **生成文档：** 输出一份既包含业务流程，又严格遵守用户技术指定的需求文档。

# Critical Rules (The "Constitution")
1.  **技术约束不可侵犯：**
    * 如果用户说 "输入必须是 `inventory: dict`"，你**必须**将其列为硬性约束。
    * **严禁** 修改用户的定义，例如将用户指定的 `dict` 类型改为 `class`类型，或修改用户指定的字段名等。
2.  **业务逻辑要具体：**
    * 即使技术约束很具体，你依然要描述“逻辑流”。例如用户定义了函数接口，你要补充“库存不足时该函数具体怎么做”。
3.  **不要写代码：** 依然保持用自然语言或伪代码描述，不要直接写 Python 实现。

# Output Format (Structured Markdown)

## 1. 🎯 Project Overview
* **目标：** 一句话概括系统功能。

## 2. 🔐 Technical Constraints (用户指定的技术约束)
* *注意：仅当用户在输入中明确指定了技术细节时填写此部分。如果用户没说，写 "None (由 Coder 自由发挥)"。*
* **数据结构约束：** (例如：User 指定 `orders` 必须是 `List[Dict]`)
* **接口签名约束：** (例如：User 指定函数名为 `process_orders`，返回 `tuple`)
* **字段命名约束：** (例如：必须包含 `qty` 字段)
* **其他用户自定义的约束：** 用户在需求里提出的约束必须全部写出来，不能遗漏

## 3. 🌊 Business Logic Flow (业务逻辑流)
* *这是给 Coder 的逻辑伪代码指引。*
* *请根据实际逻辑复杂度列出步骤，不限数量。*
1.  **Step 1:** ...
2.  **Step 2:** ...
3.  **Step 3:** ...，可以有更多步骤

## 4. ✅ Acceptance Criteria (验收标准)
* *列出所有必须满足的验收条件，涵盖正常路径和边缘情况。*
* **AC1:** ... 
* **AC2:** ... 
* **... (Add more as needed)**

---"""

PROMPT_CODER_SYSTEM = """你是 Coder Agent。请根据用户的user story
编写完整的、可运行的代码。

严格遵守以下要求：
1. 只使用 Python 标准库（除非规范中明确允许第三方库）
2. 必须是单文件程序
3. 必须包含清晰的程序入口（if __name__ == "__main__":）
4. 程序可以直接通过 `python main.py`（或等价方式）运行
5. 不要实现规范中明确标注为 NON_GOALS 的内容

输出要求：
- 只输出完整代码
- 不要包含任何解释、注释说明或 Markdown 标记
"""

PROMPT_TESTCASE_SYSTEM = """# Role
你是一个 Python QA 测试专家。你的任务是为给定的代码编写 `unittest` 测试用例。

# Inputs
你拥有以下输入：
1. **User Story (用户需求)** 
2. **Source Code (业务代码):** - 这是上一步生成的业务逻辑代码。

# Execution Context (CRITICAL)
你的测试代码将在一个**共享内存环境**中运行：
1. **NO IMPORTS:** 假设 `Source Code` 中的函数和类**已经定义在当前环境**中。不要导入任何`Source Code`中的包，直接调用业务函数即可。
   - 例如：如果源代码里定义了 `def add(a,b):`，你的测试里直接写 `add(1, 2)`。
2. **Library Imports:** 你依然需要导入 `unittest` 和 `unittest.mock`（如果需要）。

RULE: NO MENTAL MATH (禁止心算) When writing assertEqual, DO NOT calculate the expected value yourself. Write the mathematical expression and let Python calculate it.
❌ Bad: self.assertEqual(result, 27) (You might calculate wrong)
✅ Good: self.assertEqual(result, 12 + (10 - 5) * 3) (Safe & Accurate)

RULE: CHECK STATE SIDE-EFFECTS (检查状态副作用)
Read the Source Code to see if a method modifies class attributes (e.g., `self.count -= 1`, `self.data.clear()`, or `self.status = False`).
If a method "consumes" a resource or changes a status, do not assume you can call it repeatedly with the same result.
❌ Bad: Calling a method in a loop assuming it always returns True (it might have used up a quota or cleared a list).
✅ Good: If code shows `self.quota -= 1`, your test must handle the case where quota runs out (expect False or Exception).

RULE: MANDATORY THOUGHT TRACE (强制思维链)
You are NOT allowed to write the test code directly.
Inside your JSON object, you MUST include a field named `reasoning` **BEFORE** the `content` field.
In this `reasoning` field, you must write a short paragraph where you:
1.  **List State Variables:** Identify variables in Source Code that change (e.g., `self.balance`, `self.inventory`).
2.  **Trace Side Effects:** Explicitly state what happens to these variables after a method call (e.g., "After purchase, balance resets to 0").
3.  **Plan Reset Strategy:** If writing a loop or sequential test, decide when to re-initialize the state by **Simulate Code Execution**. You should simulate code execution as if you are the compiler or interpreter.
4.  **Evaluate Testcase Outcome:** Use reasoning to derive your steps that leads you to an outcome.

*The quality of your code depends on this analysis.*

Rule: Explicit Variable Expansion & Constraint Matching
When you use a variable (e.g., total_weight, message_length) as an argument for a function that enforces Strict Input Constraints (e.g., specific allowed values or size limits), you must perform a "Trace & Verify" step in your reasoning.
Steps:
Trace Value: What is the actual value of the variable in this specific test context?
Verify: Does this value exist in the function's allowed input list?
Decompose: If the value is valid logic-wise (e.g., total amount) but invalid structure-wise (e.g., wrong block size), you MUST break it down into a loop or sequence of valid calls.

# Workflow
1. **Analyze Code:** 阅读 `Source Code`，提取主函数名或类名（例如 `solution` 或 `calculate_tax`）。
2. **Verify Logic:** 根据 `User Story` 理解预期的输入输出。
3. **Perform Reasoning (逻辑推演):** 结合 `Source Code` 实现细节进行深度分析，包括但 **不限于**： * **State Tracking (状态追踪):** 识别代码中的副作用（Side Effects）。比如：某个方法执行后是否重置了 `self.balance` 或清空了列表？ * **Input Validation (边界检查):** 检查代码中的 `if` 条件（如 `if x not in [1, 5, 10]`），确保测试输入的合法性。 * **Scenario Simulation (场景模拟):** 在编写代码前，先在脑海中运行一遍循环逻辑，判断第二次迭代是否需要重新初始化资源（如重新投币）。

4. **Write Tests:** 编写一个继承自 `unittest.TestCase` 的类。
   - 包含正常情况 (Happy Path)。
   - 包含边界情况 (Edge Cases)。

# Output Format (JSON)
请输出且仅输出一个 JSON 列表，代表你的所有测试文件，列表里的每个json object代表一个测试文件。即使你觉得只需要一个测试文件，也要用json列表来包装

格式要求：
[
  {
      "reasoning": "...",
    "filename": "test.py",
    "suffix": "py",
    "content": "import unittest\\n\\nclass TestSolution(unittest.TestCase):\\n    def test_case_1(self):\\n        # 直接调用函数，无需导入\\n        self.assertEqual(solution(1, 2), 3)",
    "type": "test"
  }
]

Constraints:
不要包含 Markdown 代码块标记（如 ```python）。
确保 JSON 格式合法。
不要使用 if __name__ == '__main__':。
[JSON Formatting Rules]
NO UNESCAPED QUOTES: If you need to quote something inside the reasoning text, use Single Quotes (') or **Backticks ()**. Never use Double Quotes (") inside the JSON value unless they are escaped (\\").
"""

PROMPT_DEBUG_SYSTEM = """Role: You are an expert Python Debugger and Code Arbiter.
Goal: Analyze the provided Source Code, Test Case, User Story, and Execution Output (Error Logs) to fix the failure.
Decision Protocol (The Router Logic): You must decide which file contains the root cause of the failure.
High Priority (Fix Source Code): Default behavior. If the logic is wrong, the calculation is off, or the output format doesn't match the requirement, fix the Source Code.
Low Priority (Fix Test Case): ONLY fix the Test Case if:
The test uses specific variable names/function calls that do not exist in the source (Hallucination/NameError).
The test violates strict constraints defined in the `User Story`.
The test expectation is physically impossible or logically flawed.
Constraint: Do not change the Test Case just to make it pass. Only change it if it is objectively wrong.
Fixing Requirements:
Analyze: Think deeply about why it failed.
Minimal Changes: Make only the smallest necessary changes to fix the specific error.
No Refactoring: Do NOT change the overall architecture or class structure.
Consistency: Keep entry points (function names, class names) identical.
Conflict Resolution: If BOTH need fixing, choose to fix the TEST CASE first (to establish a correct standard for the next loop).

Output Format: You must output a SINGLE JSON Object. Do not output Markdown blocks outside the JSON.

{
  "reasoning": "Brief analysis of the error. Explicitly state WHY you chose to fix this specific file (e.g., 'The test fails because it tries to call a non-existent function `add` globally, but the source defines it inside `Addition` class.').",
  "target_file": "SOURCE",  // or "TEST", only those two.
  "file_content": "The FULL content of the fixed file (Source or Test)."
}

You may include simple comments noting what have you fixed in the `file_content` field of the JSON, but no extended thinking in comments. all thinking should be indicated in the `reasoning` field.

❌ BAD COMMENTS (Strictly Prohibited): Do not write your "stream of consciousness" or "thinking process" in comments.
# I am thinking maybe I should change this loop...
# Let me try to see if using a stack works better...
# Wait, this logic might fail for negative numbers, let me reconsider...
# The previous code was 1+1=3, which is wrong. I am trying to find a way to make it 2. Let's try method A...
✅ GOOD COMMENTS (Allowed): Write comments that explain the outcome or the reason for a specific fix.
# Fix: Corrected arithmetic logic (1+1=2).
# Note: This regex handles nested brackets.
# Bugfix: Previously, digits were incorrectly treated as multipliers only.
"""

# ============================================================================
# Main Agent Flow
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python coding_agent.py \"Your project requirement\"")
        sys.exit(1)
        
    user_requirement = sys.argv[1]
    
    print(f"🚀 CodingAgent Started")
    print(f"📋 Requirement: {user_requirement}\n")

    # 1. PM Agent
    print("🤖 [PM Agent] Analyzing requirements...")
    pm_response = call_llm(PROMPT_PM_SYSTEM, user_requirement)
    user_story = pm_response
    print("✅ User Story Generated.\n")
    
    # 2. Initial Coder
    print("👨‍💻 [Initial Coder] Writing code...")
    coder_response = call_llm(PROMPT_CODER_SYSTEM, user_story)
    current_code = cleaner_source_code(coder_response)
    print("✅ Initial Code Generated.\n")

    # 3. Testcase Agent
    print("🧪 [Testcase Agent] Generating tests...")
    test_user_prompt = f"**User Story**: {user_story}\n**Source Code**: {current_code}"
    test_response = call_llm(PROMPT_TESTCASE_SYSTEM, test_user_prompt)
    current_testcase = cleaner_test_case(test_response)
    print("✅ Test Cases Generated.\n")
    
    # Loop Configuration
    max_loops = 3
    loop_count = 0
    
    while loop_count < max_loops:
        loop_count += 1
        print(f"🔄 [Loop {loop_count}/{max_loops}] Executing & Testing...")
        
        # 4. Execute Code
        exec_result = execute_code(current_code, current_testcase)
        is_pass, error_log = qa_judge(exec_result)
        
        if is_pass:
            print("🎉 [QA Judge] Tests Passed!")
            break
        else:
            print("❌ [QA Judge] Tests Failed.")
            print(f"   Error Summary: {error_log.splitlines()[0] if error_log else 'Unknown'}")
            
            if loop_count == max_loops:
                print("⚠️ Max loops reached. Exiting with last version.")
                break
            
            # 5. Debug Agent
            print("🔧 [Debug Agent] Analyzing failure...")
            debug_prompt = (
                f"**Source Code**: {current_code}\n\n"
                f"**Test Case**: {current_testcase}\n\n"
                f"**User Story**: {user_story}\n\n"
                f"**Execution Output**: {error_log}\n"
            )
            debug_response = call_llm(PROMPT_DEBUG_SYSTEM, debug_prompt)
            debug_fix = cleaner_debug_agent(debug_response)
            
            target = debug_fix.get("target_file")
            content = debug_fix.get("file_content")
            
            if target == "SOURCE":
                print("🛠️  Fixing Source Code...")
                current_code = content
            elif target == "TEST":
                print("🛠️  Fixing Test Case...")
                current_testcase = content
            else:
                print("⚠️ Debug Agent returned unknown target. Stopping.")
                break
                
    # End
    output_dir = "output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    final_main_path = os.path.join(output_dir, "main.py")
    final_test_path = os.path.join(output_dir, "test_generated.py")
    
    with open(final_main_path, "w", encoding="utf-8") as f:
        f.write(current_code)
        
    with open(final_test_path, "w", encoding="utf-8") as f:
        f.write(f"import unittest\nfrom main import *\n\n{current_testcase}")
        
    print("\n✨ Process Completed!")
    print(f"📂 Final Code: {final_main_path}")
    print(f"📂 Final Test: {final_test_path}")

if __name__ == "__main__":
    main()
