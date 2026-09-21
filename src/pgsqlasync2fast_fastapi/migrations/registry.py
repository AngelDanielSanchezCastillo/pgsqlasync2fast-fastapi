"""
Lane-keyed chain registry (design D6 of the ``alembic-2fast`` change).

Mirrors the ``register_seeder`` idiom: consumers register their chain specs at
import time, the app composes lanes explicitly, and the runner executes them
in registration order per lane.

- AUTH lane orders ``oauth`` -> ``permissions-global`` -> ``tenant2fast-auth``
  (FK-proven); registration order within a lane is preserved.
- TENANT lane chains (``tenant2fast-rbac``, ``app``) are order-independent;
  callers may still declare an explicit preferred order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import ChainSpec

__all__ = [
    "clear_chain_registry",
    "get_lane_chains",
    "register_chain",
]

_CHAIN_REGISTRY: dict[str, list["ChainSpec"]] = {}


def register_chain(lane: str, chain: "ChainSpec") -> None:
    """
    Register a chain spec under a lane.

    Re-registering the same ``(package, script_path)`` key updates that entry
    in place (idempotent, position preserved); a distinct chain appends.
    Order of registration is the order ``get_lane_chains`` returns, which is
    the execution order for ordering-sensitive lanes.

    Args:
        lane: Lane name, e.g. ``"auth"`` or ``"tenant"``.
        chain: The chain spec to register.
    """
    chains = _CHAIN_REGISTRY.setdefault(lane, [])
    for index, existing in enumerate(chains):
        if (
            existing.package == chain.package
            and existing.script_path == chain.script_path
        ):
            chains[index] = chain
            return
    chains.append(chain)


def get_lane_chains(lane: str) -> list["ChainSpec"]:
    """
    Return a defensive copy of the chains registered for a lane.

    Args:
        lane: Lane name, e.g. ``"auth"`` or ``"tenant"``.

    Returns:
        List of ``ChainSpec`` objects in registration order (empty if none).
    """
    return list(_CHAIN_REGISTRY.get(lane, []))


def clear_chain_registry() -> None:
    """Clear all registered chains. Useful for testing."""
    global _CHAIN_REGISTRY
    _CHAIN_REGISTRY = {}