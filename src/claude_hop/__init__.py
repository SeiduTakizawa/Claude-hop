"""claude-hop: sync Claude Code sessions between two machines over SSH."""

from claude_hop.config import Config, ConfigError, default_config_path
from claude_hop.remap import PathMapper, encode_path, remap_tree

__version__ = "0.4.0"

__all__ = [
    "Config",
    "ConfigError",
    "PathMapper",
    "__version__",
    "default_config_path",
    "encode_path",
    "remap_tree",
]
