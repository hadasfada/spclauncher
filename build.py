"""SpecterCraft Launcherı derler"""

import subprocess
import sys

cmd = [sys.executable, "-m", "PyInstaller", "--clean", "spectercraft.spec"]

print("Derleniyor...")
subprocess.run(cmd, check=True)
print("Bitti dist klasöründe bulabilirsiniz.")
