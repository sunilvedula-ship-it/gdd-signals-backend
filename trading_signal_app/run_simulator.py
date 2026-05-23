import os
import sys
import time
import subprocess
import webbrowser

def main():
    print("====================================================")
    print("      GuruDevaDatta Trading Signals App Simulator   ")
    print("====================================================")
    
    # Find python executable with uvicorn and fastapi
    localappdata = os.environ.get("LOCALAPPDATA", "")
    python310_path = os.path.join(localappdata, "Microsoft", "WindowsApps", "python3.10.exe")
    python_path = os.path.join(localappdata, "Microsoft", "WindowsApps", "python.exe")
    
    # Scrub Python environment overrides to prevent child processes from inheriting default Python 3.13 path
    clean_env = os.environ.copy()
    clean_env.pop("PYTHONUSERBASE", None)
    clean_env.pop("PYTHONPATH", None)
    clean_env.pop("PYTHONHOME", None)
    
    python_cmd = [sys.executable]
    for test_cmd in [[python310_path], [python_path], [sys.executable]]:
        if os.path.exists(test_cmd[0]):
            try:
                res = subprocess.run(
                    test_cmd + ["-c", "import uvicorn, fastapi"], 
                    capture_output=True, 
                    text=True, 
                    check=True,
                    env=clean_env
                )
                python_cmd = test_cmd
                break
            except Exception as e:
                stderr_msg = getattr(e, 'stderr', '').strip()
                stdout_msg = getattr(e, 'stdout', '').strip()
                print(f"Check failed for '{test_cmd[0]}': {e}")
                if stderr_msg:
                    print(f"  Stderr: {stderr_msg}")
                if stdout_msg:
                    print(f"  Stdout: {stdout_msg}")
                continue
            
    print(f"Using Python Environment: {python_cmd[0]}")
    
    # Launch uvicorn as subprocess
    print("Starting FastAPI Backend Server...")
    proc = subprocess.Popen(
        python_cmd + ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
        env=clean_env,
        cwd=os.path.dirname(os.path.abspath(__file__))
    )




    
    # Wait for server to bind
    time.sleep(2.5)
    
    # Check if server started successfully
    if proc.poll() is not None:
        print("Error: Backend Server failed to start. Check if port 8000 is already in use.")
        return
        
    print("Backend Server is running on http://127.0.0.1:8000")
    print("Opening Interactive Simulator in browser...")
    webbrowser.open("http://127.0.0.1:8000/")
    
    print("\nPress Ctrl+C inside this window to stop the server.")
    
    try:
        while True:
            time.sleep(1)
            if proc.poll() is not None:
                print("Server stopped unexpected.")
                break
    except KeyboardInterrupt:
        print("\nStopping Backend Server...")
        if sys.platform == 'win32':
            subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("Server shutdown complete. Goodbye!")


if __name__ == '__main__':
    main()
