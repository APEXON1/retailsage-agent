import subprocess
import sys

def run_script(script_name):
    print(f"\n{'='*60}")
    print(f"▶ 执行: python {script_name}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            [sys.executable, script_name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        print(f"\n退出码: {result.returncode}")
        return result.returncode == 0
    except Exception as e:
        print(f"执行异常: {e}")
        return False

# 先运行 check_db.py
success = run_script("check_db.py")

# 如果失败，尝试 run_check.py
if not success:
    print("\n⚠️  check_db.py 执行失败，尝试运行 run_check.py...")
    run_script("run_check.py")
