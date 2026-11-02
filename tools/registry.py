"""Build the tool registry. Integrations are separate, explicit adapters."""
from __future__ import annotations

from tools.base import ToolRegistry


def build_registry() -> ToolRegistry:
    from tools.docker import docker_tools
    from tools.filesystem import tools as filesystem_tools
    from tools.git import git_tools
    from tools.integrations.ioagrco import adapter as ioagrco
    from tools.integrations.ioartifactory import adapter as ioartifactory
    from tools.integrations.iodb import adapter as iodb
    from tools.integrations.iogitlab import adapter as iogitlab
    from tools.integrations.ioprojects import adapter as ioprojects
    from tools.kubernetes import k8s_tools
    from tools.terminal import exec as terminal_tools

    registry = ToolRegistry()
    filesystem_tools.register(registry)
    git_tools.register(registry)
    terminal_tools.register(registry)
    docker_tools.register(registry)
    k8s_tools.register(registry)
    ioprojects.register(registry)
    iogitlab.register(registry)
    ioagrco.register(registry)
    ioartifactory.register(registry)
    iodb.register(registry)
    return registry
