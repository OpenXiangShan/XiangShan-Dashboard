"""Launch gem5_data_proc in this worker process for queue/cancel isolation."""
import json
import os
from pathlib import Path
import sys


def main():
    request_path = Path(sys.argv[2]).resolve()
    request = json.loads(request_path.read_text())
    entry = Path(request["processor"]) / "analysis_pipeline.py"
    if not entry.is_file():
        raise SystemExit("gem5_data_proc 缺少 analysis_pipeline.py，请更新处理器 checkout")
    os.execv(sys.executable, [sys.executable, str(entry), sys.argv[1], str(request_path)])


if __name__ == "__main__":
    main()
