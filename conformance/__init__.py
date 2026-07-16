"""Runnable Workspace Core conformance consumer used by issue #772 proof."""

from .application import ConformanceRuntime, ConformanceSettings, create_conformance_runtime

__all__ = ["ConformanceRuntime", "ConformanceSettings", "create_conformance_runtime"]
