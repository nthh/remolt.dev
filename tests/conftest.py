import sys
from pathlib import Path

# Add project root so `import server.server` works
sys.path.insert(0, str(Path(__file__).parent.parent))
