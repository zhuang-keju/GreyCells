from datasets import load_dataset
import json
import os
from coding_agent import main as run_greycells
from coding_agent import execute_code # 引入 execute_code 用于外部验证
all_stats = []
def verify_with_canonical_test(generated_code, problem):
    """
    使用 HumanEval 官方提供的测试用例进行外部验证
    """
    # HumanEval 的 test 字段包含 check 函数，我们需要把生成的代码拼上去
    # 并显式调用 check(entry_point)
    verification_content = f"""
{generated_code}

{problem['test']}

check({problem['entry_point']})
"""
    # 构造沙箱执行包
    source_dict = {
        "content": verification_content,
        "filename": "final_verify.py",
        "packages": [] 
    }
    dummy_test = {"filename": "dummy.py", "content": "import unittest\nclass D(unittest.TestCase): pass"}
    
    # 静默执行
    result = execute_code(source_dict, dummy_test, run_command="python final_verify.py")
    print(result)
    return result.get("is_pass", False), result.get("stderr", "")

def run_humaneval_benchmark(dataset, limit=5):
    results = []
    is_break = False
    # [FIX 1] 使用 range 遍历，避免字典切片陷阱
    for i in range(min(limit, len(dataset))):
        problem = dataset[i]
        task_id = problem["task_id"]
        user_prompt = problem["prompt"]
        
        print(f"\n" + "="*50)
        print(f"📋 处理任务: {task_id}")
        print("="*50)

        try:
            # [FIX 2] 删除不存在的 return_code 参数
            outcome = run_greycells(prompt=user_prompt, max_loop_count=3, return_code=True)
            
            stats = {"task_id": task_id,"tokens": outcome["tokens"], "loops": outcome["loops"], "calls": outcome["calls"]}
            all_stats.append(stats)
            internal_pass = outcome.get("is_pass", False)
            generated_code = outcome.get("code", {}).get("content", "")

            # [FIX 3] 增加外部验证 (Ground Truth)
            external_pass = False
            error_msg = ""
            
            if internal_pass:
                print(f"   ↳ 内部测试通过，正在进行官方验证...")
            else:
                print(f"   ↳ 内部测试未通过...")
                break

            external_pass, error_msg = verify_with_canonical_test(generated_code, problem)
            
            status = "❌ FAIL"
            if internal_pass and external_pass:
                status = "✅ PASS"
            elif internal_pass and not external_pass:
                status = "⚠️ FALSE POSITIVE" # 内部过了，外部没过（幻觉）
            
            print(f"   ↳ 结果: {status}")

            results.append({
                "task_id": task_id,
                "internal_pass": internal_pass,
                "external_pass": external_pass
            })
                    
        except Exception as e:
            print(f"💥 异常: {e}")
            results.append({"task_id": task_id, "internal_pass": False, "external_pass": False, "error": str(e)})
            # with open("logs")

    # 打印最终报告
    print("\n" + "📊" * 5 + " BENCHMARK REPORT " + "📊" * 5)
    total = len(results)
    internal_ok = sum(1 for r in results if r["internal_pass"])
    real_pass = sum(1 for r in results if r["external_pass"])
    
    print(f"总任务数: {total}")
    print(f"内部通过率 (Self-Reported): {internal_ok}/{total}")
    print(f"真实通过率 (HumanEval Score): {real_pass}/{total}  <-- 这是你的论文分数")

if __name__ == "__main__":
    # 加载数据集
    print("📥 Loading HumanEval...")
    dataset = load_dataset("openai/openai_humaneval")
    # 传入 test split
    run_humaneval_benchmark(dataset['test'], limit=10)
    print(all_stats)