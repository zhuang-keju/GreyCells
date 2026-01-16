
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





def cleaner_source_code(llm_output: str) -> dict:
    """Logic from 'Code Cleaner (1)' node."""
    code = extract_markdown_code(llm_output)
    
    try:
        # Try pure JSON load first
        data = json.loads(code)
    except:
        # If strict JSON fails, try the regex decode fallback from YAML
        # (Simplified here to just look for content pattern as the regex in YAML was complex)
        # However, for robustness, let's assume the LLM follows instructions reasonably well
        # or that we can fix simple JSON errors.
        print("  [Cleaner] Warning: JSON decode failed for [source code], trying REGEX extraction...")
        # Fallback: simple text extraction if it looks like code
        data = extract_json_regex(code, {"packages": "list", "content": "string", "suffix": "string", "filename": "string"})


    if isinstance(data, list):
        data = data[0]

    assert data["content"]
    assert data["filename"]
    if isinstance(data, dict):
        data["content"] = remove_trailing_slash(data["content"])
    
    return data




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
        print("  [Cleaner] Warning: JSON decode failed for testcase, trying REGEX extraction...")
        # Fallback: simple text extraction if it looks like code
        
        data = extract_json_regex(code, {"content": "string", "suffix": "string", "filename": "string"})


    if isinstance(data, list):
        data = data[0]

    assert data["content"]
    assert data["filename"]
    if isinstance(data, dict):
        data["content"] = remove_trailing_slash(data["content"])
    
    return data



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

def execute_code(source_code: dict, test_code: dict) -> Dict[str, Any]:
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
        try:
            proc = sandbox.commands.run(f"python -m unittest {test_filename}", timeout=30)
            
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
    PROMPT_DEBUG_SYSTEM,
)

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
    with open("pm.txt", "w", encoding="utf-8") as f:
        f.write(pm_response)
    user_story = pm_response
    print("✅ User Story Generated.\n")
    
    # 2. Initial Coder
    print("👨‍💻 [Initial Coder] Writing code...")
    coder_response = call_llm(PROMPT_CODER_SYSTEM, user_story)
    with open("coder.txt", "w", encoding="utf-8") as f:
        f.write(coder_response)

    current_code = cleaner_source_code(coder_response)
    print("✅ Initial Code Generated.\n")

    # 3. Testcase Agent
    print("🧪 [Testcase Agent] Generating tests...")
    test_user_prompt = f"**User Story**: {user_story}\n**Source Code**: {current_code['content']}"
    test_response = call_llm(PROMPT_TESTCASE_SYSTEM, test_user_prompt)
    with open("test.txt", "w", encoding="utf-8") as f:
        f.write(test_response)

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
            print(exec_result)

            break
        else:
            print("❌ [QA Judge] Tests Failed.")
            print(exec_result)
            print(f"   Error Summary: {error_log.splitlines()[0] if error_log else 'Unknown'}")
            
            if loop_count == max_loops:
                print("⚠️ Max loops reached. Exiting with last version.")
                break
            
            # 5. Debug Agent
            print("🔧 [Debug Agent] Analyzing failure...")
            debug_prompt = (
                f"**Source Code**: {current_code['content']}\n\n"
                f"**Test Case**: {current_testcase['content']}\n\n"
                f"**User Story**: {user_story}\n\n"
                f"**Execution Output**: {error_log}\n"
            )
            debug_response = call_llm(PROMPT_DEBUG_SYSTEM, debug_prompt)
            with open(f"debug_{loop_count}.txt", "w", encoding="utf-8") as f:
                f.write(debug_response)

            debug_fix = cleaner_debug_agent(debug_response)
            
            target = debug_fix.get("target_file")
            content = debug_fix.get("file_content")
            
            if target == "SOURCE":
                print("🛠️  Fixing Source Code...")
                current_code["content"] = content #, "packages": current_code["packages"]}
            elif target == "TEST":
                print("🛠️  Fixing Test Case...")
                current_testcase["content"] = content
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

if __name__ == "__main__":
    main()
