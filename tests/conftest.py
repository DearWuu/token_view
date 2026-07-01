"""pytest 共享配置。"""
import sys
from pathlib import Path

# 把项目根加进 sys.path，pytest 不一定能自动发现
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
