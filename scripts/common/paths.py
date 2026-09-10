"""Project paths shared by development tools, independent of process cwd."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "assets" / "templates"
