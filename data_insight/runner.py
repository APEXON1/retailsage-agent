import subprocess
import sys
import os

def run_script(script_name, label):
    print(f"\n{'='*60}")
    print(f"▶ 执行: python {script_name}")
    print(f"{'='*60}")
    try:
        # 使用当前 Python 解释器
        result = subprocess.run(
            [sys.executable, script_name],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(os.path.abspath(__file__)) or '.'
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        print(f"退出码: {result.returncode}")
        return result.returncode == 0
    except Exception as e:
        print(f"执行异常: {e}")
        return False

# 先运行 check_db.py
success = run_script("check_db.py", "check_db")

# 如果失败，尝试 run_check.py
if not success:
    print("\n⚠️  check_db.py 执行失败，尝试运行 run_check.py...")
    run_script("run_check.py", "run_check")

print("\n✅ 全部执行完毕")
