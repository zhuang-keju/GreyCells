
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
from markdown_it import MarkdownIt



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

def call_llm(system_prompt: str, user_prompt: str, current_task_stats = None) -> str:
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
                if current_task_stats is not None:
                    usage = result_json.get('usageMetadata', {})
                    total_tokens = usage.get('totalTokenCount', 0)
                    current_task_stats["tokens"] += total_tokens
                    current_task_stats["calls"] += 1
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



def validate_test_quality(original_test_code, new_test_code):
    """
    宪兵：检查 Executor 是否把 PBT 删了。
    """
    # 关键词特征 (根据你的 PBT 风格调整)
    pbt_keywords = ["random.", "hypothesis", "for _ in range", "for i in range"]
    
    # 1. 检查新代码里有没有 PBT 特征
    has_pbt = any(k in new_test_code for k in pbt_keywords)
    
    if not has_pbt:
        # 如果原代码里有，新代码里没了 -> 报警！
        if any(k in original_test_code for k in pbt_keywords):
            return False, "⚠️ Error: You removed the Property-Based Testing (random/loop) logic! Restore it immediately."
            
    return True, "Pass"


# extract one json object only
def extract_json_regex(text: str, schema: Dict[str, str]) -> Dict[str, Any]:
    """
    基于预定义类型的通用正则提取器。
    
    Args:
        text: LLM 返回的原始文本
        schema: 字段名到类型的映射，支持 'string', 'list', 'bool'
                例如: {"filename": "string", "packages": "list"}
    
    Returns:
        提取到的数据字典。未匹配到的字段将根据类型返回默认值 (空字符串或空列表)。
    """
    result = {}
    
    for field, field_type in schema.items():
        if field_type == "string":
            # 匹配字符串: "key": "value"
            # (?<!\\)" 确保不匹配转义的引号
            pattern = fr'"{field}"\s*:\s*"(.*?)(?<!\\)"'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                result[field] = safe_json_decode(match.group(1))
            else:
                result[field] = ""
                
        elif field_type == "list":
            # 匹配列表: "key": [...]
            # (.*?) 非贪婪匹配直到遇到闭合的 ]
            pattern = fr'"{field}"\s*:\s*\[(.*?)\]'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                raw_content = match.group(1)
                # 提取列表内的所有字符串项 (支持双引号和单引号)
                items = re.findall(r'["\']([^"\']+)["\']', raw_content)
                # 对每一项进行解码
                result[field] = [safe_json_decode(item) for item in items]
            else:
                result[field] = []

        elif field_type == "bool":
            # 匹配布尔值: "key": true/false
            pattern = fr'"{field}"\s*:\s*(true|false)'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result[field] = match.group(1).lower() == "true"
            else:
                result[field] = False # 默认值
                
    return result




def parse_markdown_with_schema(llm_output: str, schema: dict) -> dict:
    """
    通用 Markdown 解析器 (基于 AST)。
    
    Args:
        llm_output (str): LLM 输出的原始 Markdown 文本。
        schema (dict): 提取规则配置。
            格式: { "HeaderName": ("target_key", "type") }
            - HeaderName: Markdown 中的标题 (不区分大小写).
            - target_key: 输出字典中的 key.
            - type: 提取模式 ("text" | "code" | "json").
            
    Returns:
        dict: 解析后的结果字典。
    """
    md = MarkdownIt("commonmark", {"breaks": True, "html": True})
    tokens = md.parse(llm_output)
    

    # === 🛡️ 核心修复：检测是否被套了一层 Markdown 外壳 ===
    # 逻辑：如果 Token 很少（通常只有1个 fence），且这个 fence 的内容里包含 "## Reasoning"，说明被套娃了
    if len(tokens) == 1 and tokens[0].type == 'fence':
        inner_content = tokens[0].content
        # 简单的嗅探，看里面有没有我们的关键词
        if "## Reasoning" in inner_content or "## Content" in inner_content:
            print("⚠️ Detected nested Markdown wrapper. Peeling it off and re-parsing...")
            return parse_markdown_with_schema(inner_content, schema) # <--- 递归调用！


    # 初始化结果字典
    result = {config[0]: None for config in schema.values()}
    
    # 状态机变量
    current_target_key = None
    current_type = None
    
    # 预处理 Schema: 建立 "小写标题 -> (target_key, type)" 的映射，实现标题大小写不敏感
    normalized_schema = {k.lower(): v for k, v in schema.items()}

    for i, token in enumerate(tokens):
        # 1. 发现标题 (H1-H6)
        if token.type == 'heading_open':
            # 获取标题文本 (heading_open 的下一个 token 必然是 inline)
            # 这是一个 Look-ahead 操作
            if i + 1 < len(tokens) and tokens[i+1].type == 'inline':
                title_text = tokens[i+1].content.strip().lower()
                
                # 检查这个标题是否在我们的 Schema 里
                if title_text in normalized_schema:
                    target_key, extract_type = normalized_schema[title_text]
                    current_target_key = target_key
                    current_type = extract_type
                else:
                    # 遇到了不在 Schema 里的标题，停止当前收集
                    current_target_key = None
            continue

        # 2. 收集内容 (根据当前的 target_key 和 type)
        if current_target_key:
            
            # === 模式 A: 提取纯文本 (Reasoning) ===
            if current_type == "text":
                # 收集段落文本、行内文本
                if token.type == 'inline':
                    content = token.content
                    # 初始化或追加
                    if result[current_target_key] is None:
                        result[current_target_key] = content
                    else:
                        result[current_target_key] += "\n" + content
            
            # === 模式 B: 提取代码块 (Content) ===
            elif current_type == "code":
                if token.type == 'fence':
                    # 直接提取代码块内容，忽略语言标记
                    result[current_target_key] = token.content.strip()
            
            # === 模式 C: 提取 JSON (Metadata) ===
            elif current_type == "json":
                if token.type == 'fence':
                    try:
                        # 尝试解析 JSON
                        json_content = json.loads(token.content.strip())
                        result[current_target_key] = json_content
                    except json.JSONDecodeError:
                        print(f"⚠️ 解析错误: 标题 '{current_target_key}' 下的代码块不是合法 JSON。")
                        result[current_target_key] = {} # 失败返回空字典

    return result



def cleaner_source_code(llm_output: str) -> dict:
    """Logic from 'Code Cleaner (1)' node."""
    print("=============================================")
    llm_output = llm_output.strip("\n`markdown") + "\n```"
    # print(llm_output)

    print("=============================================")


    coder_schema = {
    "Reasoning": ("reasoning", "text"),  # 提取 ## Reasoning 下的文本 -> result['reasoning']
    "Content":   ("content", "code"),    # 提取 ## Content 下的代码块 -> result['content']
    "Metadata":  ("metadata", "json")    # 提取 ## Metadata 下的JSON -> result['metadata']
    }

    print(f"🧹 [Cleaner] Parsing Coder Output...")
    cleaned_data = parse_markdown_with_schema(llm_output, coder_schema)
    meta = cleaned_data.get("metadata", {})
    source_code = cleaned_data.get("content")
    if not meta or not source_code:
        with open("logs.txt", "a") as f:
            f.write("================================================\n" * 2+llm_output)
    print(meta)
    print(source_code[:100])
    # if not meta:
        # print("=============================================")
        # print(llm_output)
        # print("=============================================")

    meta["content"] = source_code
    return meta




def cleaner_test_case(llm_output: str) -> dict:
    """Logic from 'Test Cleaner' node."""
    print("=============================================")
    llm_output = llm_output.strip("\n`markdown") + "\n```"
    # print(llm_output)

    print("=============================================")

    # 1. 定义 Schema (对应 Testcase Agent Prompt 的 Markdown 标题)
    test_schema = {
        "Reasoning": ("reasoning", "text"),  # 提取 ## Reasoning 下的测试策略
        "Content":   ("content", "code"),    # 提取 ## Content 下的测试代码
        "Metadata":  ("metadata", "json")    # 提取 ## Metadata 下的元数据
    }

    print(f"🧹 [Cleaner] Parsing Test Case Output...")
    
    # 2. 调用通用解析器 (上一轮实现的 parse_markdown_with_schema)
    cleaned_data = parse_markdown_with_schema(llm_output, test_schema)
    
    # 3. 提取数据
    meta = cleaned_data.get("metadata", {})
    test_code = cleaned_data.get("content")
    reasoning = cleaned_data.get("reasoning")
    if not meta or not test_code or not reasoning:
        with open("logs.txt", "a") as f:
            f.write("================================================\n" * 2+llm_output)

    # if not meta:
        # print("=============================================")
        # print(llm_output)
        # print("=============================================")

    # 4. 打印调试信息
    print(f"   Target File: {meta.get('filename', 'test.py')}")
    if test_code:
        # 只打印前 80 个字符预览
        print(f"   Code Preview: {test_code[:80].replace(chr(10), ' ')}...")
    else:
        print("   ⚠️ Warning: No test code found in Markdown content!")

    # 5. 组装/扁平化 (Flatten)
    # 将提取到的代码和推理过程合并进 meta 字典，方便后续节点使用
    meta["content"] = test_code
    meta["reasoning"] = reasoning # 把 PBT 的推理思路也存下来，Debug 时很有用
    
    # 兜底：如果 LLM 忘了写 filename，默认给一个
    if "filename" not in meta or not meta["filename"]:
        meta["filename"] = "test.py"

    return meta




def cleaner_debug_agent(llm_output: str) -> dict:
    """Logic from 'Debug Cleaner' node."""

    print("=============================================")
    llm_output = llm_output.strip("\n`markdown") + "\n```"
    # print(llm_output)

    print("=============================================")
    


    # 1. 定义 Schema (严格对应 PROMPT_DEBUG_SYSTEM 中的 Markdown 标题)
    debug_schema = {
        "Reasoning":    ("reasoning", "text"),      # 对应 ## Reasoning
        "Target_file":  ("target_file", "text"),    # 对应 ## Target_file (SOURCE/TEST)
        "File_content": ("file_content", "code")    # 对应 ## File_content (提取代码块)
    }

    print(f"🧹 [Cleaner] Parsing Debug Output...")
    
    # 2. 调用通用解析器
    cleaned_data = parse_markdown_with_schema(llm_output, debug_schema)
    
    # 3. 提取并清洗数据
    reasoning = cleaned_data.get("reasoning", "")


    
    # 清洗 Target_file: 移除可能存在的空格、换行或 Markdown 加粗符号 (**SOURCE**)
    raw_target = cleaned_data.get("target_file", "").strip().upper().replace("*", "")
    if "SOURCE" in raw_target:
        target_file = "SOURCE"
    elif "TEST" in raw_target:
        target_file = "TEST"
    # target_file = raw_target.strip().upper().replace("*", "") if raw_target else "SOURCE"
    
    # 提取代码内容
    file_content = cleaned_data.get("file_content")

    if not reasoning or not raw_target or not target_file:
        with open("logs.txt", "a") as f:
            f.write("================================================\n" * 2+llm_output)

    # 4. 打印调试信息 (Log)
    print(f"   🎯 Decision: Fix {target_file}")
    print(f"   🧠 Reasoning: {reasoning.strip().splitlines()[0]}..." if reasoning else "   No reasoning found")
    
    if file_content:
        print(f"   📝 Code Preview: {file_content[:60].replace(chr(10), ' ')}...")
    else:
        print("   ⚠️ Warning: No code content found in Debug output!")

    # 5. 返回标准化字典
    return {
        "target_file": target_file,
        "file_content": file_content,
        "reasoning": reasoning
    }




def cleaner_debug_test(llm_output: str) -> dict:
    """Logic from 'Debug Cleaner' node."""

    print("=============================================")
    llm_output = llm_output.strip("\n`markdown") + "\n```"
    # print(llm_output)

    print("=============================================")
    


    # 1. 定义 Schema (严格对应 PROMPT_DEBUG_SYSTEM 中的 Markdown 标题)
    debug_schema = {
        "Reasoning":    ("reasoning", "text"),      # 对应 ## Reasoning
        "Decision":  ("decision", "text"),    # 对应 ## Decision
        "File_content": ("file_content", "code")    # 对应 ## File_content (提取代码块)
    }

    print(f"🧹 [Cleaner] Parsing Debug [TESTCASE] Output...")
    
    # 2. 调用通用解析器
    cleaned_data = parse_markdown_with_schema(llm_output, debug_schema)
    
    # 3. 提取并清洗数据
    reasoning = cleaned_data.get("reasoning", "")


    
    # 清洗 Target_file: 移除可能存在的空格、换行或 Markdown 加粗符号 (**SOURCE**)
    decision = cleaned_data.get("decision", "").strip().upper().replace("*", "")
    if "FIX" in decision:
        decision = "FIX"
    elif "REMAIN" in decision:
        decision = "REMAIN"
    # target_file = raw_target.strip().upper().replace("*", "") if raw_target else "SOURCE"
    
    # 提取代码内容
    file_content = cleaned_data.get("file_content")

    if not reasoning or not decision or not file_content:
        with open("logs.txt", "a") as f:
            f.write("================================================\n" * 2+llm_output)

    # 4. 打印调试信息 (Log)
    print(f"   🎯 Decision: Fix {decision}")
    print(f"   🧠 Reasoning: {reasoning.strip().splitlines()[0]}..." if reasoning else "   No reasoning found")
    
    if file_content:
        print(f"   📝 Code Preview: {file_content[:60].replace(chr(10), ' ')}...")
    else:
        print("   ⚠️ Warning: No code content found in TESTCASE output!")

    # 5. 返回标准化字典
    return {
        "decision": decision,
        "file_content": file_content,
        "reasoning": reasoning
    }




def cleaner_debug_source(llm_output: str) -> dict:
    """Logic from 'Debug Cleaner' node."""

    print("=============================================")
    llm_output = llm_output.strip("\n`markdown") + "\n```"
    # print(llm_output)

    print("=============================================")
    


    # 1. 定义 Schema (严格对应 PROMPT_DEBUG_SYSTEM 中的 Markdown 标题)
    debug_schema = {
        "Reasoning":    ("reasoning", "text"),      # 对应 ## Reasoning
        "Decision":  ("decision", "text"),    # 对应 ## Decision
        "File_content": ("file_content", "code")    # 对应 ## File_content (提取代码块)
    }

    print(f"🧹 [Cleaner] Parsing Debug [SOURCE] Output...")
    
    # 2. 调用通用解析器
    cleaned_data = parse_markdown_with_schema(llm_output, debug_schema)
    
    # 3. 提取并清洗数据
    reasoning = cleaned_data.get("reasoning", "")


    
    # 清洗 Target_file: 移除可能存在的空格、换行或 Markdown 加粗符号 (**SOURCE**)
    decision = cleaned_data.get("decision", "").strip().upper().replace("*", "")
    if "FIX" in decision:
        decision = "FIX"
    elif "VETO" in decision:
        decision = "VETO"
    # target_file = raw_target.strip().upper().replace("*", "") if raw_target else "SOURCE"
    
    # 提取代码内容
    file_content = cleaned_data.get("file_content")

    if not reasoning or not decision or not file_content:
        with open("logs.txt", "a") as f:
            f.write("================================================\n" * 2+llm_output)

    # 4. 打印调试信息 (Log)
    print(f"   🎯 Decision: Fix {decision}")
    print(f"   🧠 Reasoning: {reasoning.strip().splitlines()[0]}..." if reasoning else "   No reasoning found")
    
    if file_content:
        print(f"   📝 Code Preview: {file_content[:60].replace(chr(10), ' ')}...")
    else:
        print("   ⚠️ Warning: No code content found in SOURCE output!")

    # 5. 返回标准化字典
    return {
        "decision": decision,
        "file_content": file_content,
        "reasoning": reasoning
    }






# ============================================================================
# Tool: Code Execution (Subprocess)
# ============================================================================

def execute_code(source_code: dict, test_code: dict, run_command=None) -> Dict[str, Any]:
    """
    Simulates the Vercel Code Runner.
    Writes code to temp files and runs unittest.
    """

    with Sandbox.create(api_key=E2B_API_KEY) as sandbox:
        print("🚀 沙箱已启动...")

        packages = source_code["packages"]

        if packages:
            package_str = " ".join(packages)
            print(f"installing {package_str}")
            install_cmd = f"pip install {package_str}"
            sandbox.commands.run(install_cmd, timeout=120)

        filename = source_code["filename"]
        sandbox.files.write(filename, source_code["content"])
        print(f"✅ 文件 {filename} 已写入沙箱")

        test_filename = test_code["filename"]
        test_file_content = f"import unittest\nfrom {filename.split('.')[0]} import *\n\n" + test_code["content"]
        sandbox.files.write(test_filename, test_file_content)
        print(f"✅ 文件 {test_filename} 已写入沙箱")


        # 3. 执行代码 (替代 subprocess 的部分)
        # 注意：timeout 参数直接在这里设置，单位是秒
        if run_command is None:
            cmd = f"python -m unittest {test_filename}"
        else:
            cmd = run_command
        try:
            proc = sandbox.commands.run(cmd, timeout=30)
            
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

from prompts import (
    PROMPT_PM_SYSTEM,
    PROMPT_CODER_SYSTEM,
    PROMPT_TESTCASE_SYSTEM,
    # PROMPT_DEBUG_SYSTEM,
    PROMPT_DEBUG_TESTCASE,
    PROMPT_DEBUG_SOURCE,
)

# ============================================================================
# Main Agent Flow
# ============================================================================

def main(prompt=None,max_loop_count=3, return_code=False, return_stats=True):
    if len(sys.argv) < 2:
        if prompt is None:
            print("Usage: python coding_agent.py \"Your project requirement\"")
            sys.exit(1)
        else: user_requirement = prompt
    else:
        user_requirement = sys.argv[1]
    
    current_task_stats = {
        "tokens": 0,
        "loops": 0,
        "calls": 0,
    }

    print(f"🚀 CodingAgent Started")
    print(f"📋 Requirement: {user_requirement}\n")

    # 1. PM Agent
    print("🤖 [PM Agent] Analyzing requirements...")
    pm_response = call_llm(PROMPT_PM_SYSTEM, user_requirement, current_task_stats)
    with open("pm.txt", "w", encoding="utf-8") as f:
        f.write(pm_response)
    user_story = pm_response
    print("✅ User Story Generated.\n")
    
    # 2. Initial Coder
    print("👨‍💻 [Initial Coder] Writing code...")
    coder_response = call_llm(PROMPT_CODER_SYSTEM, user_story, current_task_stats)
    with open("coder.txt", "w", encoding="utf-8") as f:
        f.write(coder_response)

    current_code = cleaner_source_code(coder_response)
    print("✅ Initial Code Generated.\n")

    # 3. Testcase Agent
    print("🧪 [Testcase Agent] Generating tests...")
    test_user_prompt = f"**User Story**: {user_story}\n**Source Code**: {current_code['content']}"
    test_response = call_llm(PROMPT_TESTCASE_SYSTEM, test_user_prompt, current_task_stats)
    with open("test.txt", "w", encoding="utf-8") as f:
        f.write(test_response)

    current_testcase = cleaner_test_case(test_response)
    print("✅ Test Cases Generated.\n")
    
    # Loop Configuration
    max_loops = max_loop_count
    loop_count = 0
    final_success = False

    while loop_count < max_loops:
        loop_count += 1
        print(f"🔄 [Loop {loop_count}/{max_loops}] Executing & Testing...")
        
        # 4. Execute Code
        exec_result = execute_code(current_code, current_testcase)
        is_pass, error_log = qa_judge(exec_result)
        


        if is_pass:
            print("🎉 [QA Judge] Tests Passed!")
            print(exec_result)
            final_success = True # 标记成功
            break
        # else:
        print("❌ [QA Judge] Tests Failed.")
        print(exec_result)
        print(f"   Error Summary: {error_log.splitlines()[0] if error_log else 'Unknown'}")
        
        if loop_count == max_loops:
            print("⚠️ Max loops reached. Exiting with last version.")
            break
        
        # 5. Debug Agent
        print("🔧 [Debug Agent] Analyzing failure in TESTCASE...")
        debug_prompt = (
            f"**Source Code**: {current_code['content']}\n\n"
            f"**Test Case**: {current_testcase['content']}\n\n"
            f"**User Story**: {user_story}\n\n"
            f"**Execution Output**: {error_log}\n"
        )
        debug_response = call_llm(PROMPT_DEBUG_TESTCASE, debug_prompt, current_task_stats)
        with open(f"debug_test_{loop_count}.txt", "w", encoding="utf-8") as f:
            f.write(debug_response)

        debug_fix = cleaner_debug_test(debug_response)
        
        decision = debug_fix.get("decision")
        content = debug_fix.get("file_content")
        
        if decision == "FIX":
            print("🛠️  Fixing Testcase Code...")
            current_testcase["content"] = content #, "packages": current_code["packages"]}
            re_execute = True
        elif decision == "REMAIN":
            print("Test Case has no error, proceed to next step...")
            re_execute = False
            # current_testcase["content"] = content
        else:
            print("⚠️ Debug Agent returned unknown target. Stopping.")
            break
        
        if re_execute:
            exec_result = execute_code(current_code, current_testcase)
            is_pass, error_log = qa_judge(exec_result)
        
            if is_pass:
                print("🎉 [QA Judge] Tests Passed!")
                print(exec_result)
                final_success = True # 标记成功
                break
            # else:
            print("❌ [QA Judge] Tests Failed.")
            print(exec_result)
            print(f"   Error Summary: {error_log.splitlines()[0] if error_log else 'Unknown'}")


        # 6. Debug Agent
        print("🔧 [Debug Agent] Analyzing failure in SOURCE...")
        debug_prompt = (
            f"**Source Code**: {current_code['content']}\n\n"
            f"**Test Case**: {current_testcase['content']}\n\n"
            f"**User Story**: {user_story}\n\n"
            f"**Execution Output**: {error_log}\n"
        )
        debug_response = call_llm(PROMPT_DEBUG_SOURCE, debug_prompt, current_task_stats)
        with open(f"debug_source_{loop_count}.txt", "w", encoding="utf-8") as f:
            f.write(debug_response)

        debug_fix = cleaner_debug_source(debug_response)
        
        decision = debug_fix.get("decision")
        content = debug_fix.get("file_content")
        
        if decision == "FIX":
            print("🛠️  Fixing Source Code...")
            current_code["content"] = content #, "packages": current_code["packages"]}
            # re_execute = True
        elif decision == "VETO":
            print("Source Code has no error, proceed to next step...")
            # re_execute = False
            # current_testcase["content"] = content
        else:
            print("⚠️ Debug Agent returned unknown target. Stopping.")
            break



    # End
    output_dir = "output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    filename = current_code["filename"]
    test_filename = current_testcase["filename"]

    final_main_path = os.path.join(output_dir, filename)
    final_test_path = os.path.join(output_dir, test_filename)

    if current_code['packages']:
        print("Writing requirements.txt")
        final_requirement_path = os.path.join(output_dir, "requirements.txt")
        with open(final_requirement_path, "w", encoding="utf-8") as f:
            f.write("\n".join(current_code['packages']))


    with open(final_main_path, "w", encoding="utf-8") as f:
        f.write(current_code["content"])
        
    with open(final_test_path, "w", encoding="utf-8") as f:
        f.write(f"import unittest\nfrom {filename.split('.')[0]} import *\n\n{current_testcase['content']}")
        
    print("\n✨ Process Completed!")
    print(f"📂 Final Code: {final_main_path}")
    print(f"📂 Final Test: {final_test_path}")

    current_task_stats["loops"] = loop_count + (not final_success)
    result_code_dict = {
                "is_pass": final_success,
                "code": current_code,
                "test": current_testcase,
                # "task_id": getattr(main, "current_task_id", "unknown") # 可选：记录任务ID
            }
    if return_code:
        if return_stats:
            current_task_stats.update(result_code_dict)
            return current_task_stats
        else:
            return result_code_dict
    else:
        if return_stats:
            return current_task_stats

if __name__ == "__main__":
    main()
