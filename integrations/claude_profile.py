"""Documented Claude parameter compatibility, never a provenance assertion.

References reviewed 2026-09-29:
https://platform.claude.com/docs/en/build-with-claude/thinking
https://platform.claude.com/docs/en/build-with-claude/working-with-messages
https://platform.claude.com/docs/en/build-with-claude/prompt-caching

Unknown relay aliases remain unknown; their advertised spelling cannot prove
which model runs behind an endpoint. Runtime errors retain their evidence.
"""
import re


def _unknown():
    return {"known": False, "thinking": "unknown", "prefill": None,
            "sampling": None, "forced_tools": None,
            "preserves_prior_thinking": None, "cache_min_tokens": None}


def model_capabilities(model):
    name = str(model).lower().replace("_", "-")
    match = re.search(r"claude[-.]?(opus|sonnet|haiku|fable|mythos)[-.](\d+)(?:[-.](\d)(?!\d))?", name)
    if match:
        family, major, minor = match.groups()
        version = (int(major), int(minor or 0))
    else:
        legacy = re.search(r"claude[-.](\d+)(?:[-.](\d)(?!\d))?[-.](opus|sonnet|haiku)", name)
        if legacy:
            major, minor, family = legacy.groups()
            version = (int(major), int(minor or 0))
        elif "claude-mythos-preview" in name:
            family, version = "mythos", (0, 0)
        else:
            return _unknown()
    documented = {
        "opus": {(3,0),(4,0),(4,1),(4,5),(4,6),(4,7),(4,8),(5,0),(5,5)},
        "sonnet": {(3,0),(3,5),(3,7),(4,0),(4,5),(4,6),(5,0),(5,5)},
        "haiku": {(3,0),(3,5),(4,5)}, "fable": {(5,0),(5,1)},
        "mythos": {(0,0),(5,0),(5,1)},
    }
    if version not in documented[family]: return _unknown()
    preview = family == "mythos" and version == (0, 0)
    adaptive = preview or version >= (4, 6) or family in ("fable", "mythos")
    thinking = "adaptive" if adaptive else "enabled" if version >= (3, 7) else None
    forced = not ((family in ("opus", "sonnet") and version >= (5, 5)) or
                  (family in ("fable", "mythos") and version >= (5, 1)))
    always_on = preview or family in ("fable", "mythos") or (family in ("opus", "sonnet") and version >= (5, 5))
    if preview or (family == "opus" and version == (4, 7)):
        cache_min = 2048
    elif (family == "opus" and version in ((4, 5), (4, 6))) or (family == "haiku" and version >= (4, 5)):
        cache_min = 4096
    elif (family in ("fable", "mythos") or family == "opus" and version >= (5, 0) or family == "sonnet" and version >= (5, 5)):
        cache_min = 512
    elif family == "haiku" and version == (3, 5):
        cache_min = 2048
    else:
        cache_min = 1024
    return {"known": True, "family": family, "version": version,
            "thinking": thinking, "thinking_always_on": always_on,
            "prefill": not adaptive, "sampling": not (preview or version >= (4, 7)),
            "forced_tools": forced,
            "preserves_prior_thinking": (family == "opus" and version >= (4, 5)) or
                                        (family == "sonnet" and version >= (4, 6)) or family in ("fable", "mythos"),
            "cache_min_tokens": cache_min}


def native_plain_options(model):
    """Valid ordinary-probe controls; never disable an always-on model."""
    profile = model_capabilities(model)
    if profile["known"] and profile["thinking"] and not profile["thinking_always_on"]:
        result = {"thinking": {"type": "disabled"}}
        if profile["version"] >= (5, 0):
            result["output_config"] = {"effort": "high"}
        return result
    return {}


def native_thinking_options(model):
    profile = model_capabilities(model)
    if profile["thinking"] == "enabled":
        return {"thinking": {"type": "enabled", "budget_tokens": 1024}}
    if profile["thinking"] is None:
        return {}
    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}
