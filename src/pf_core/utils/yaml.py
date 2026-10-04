"""YAML loading that refuses a repeated mapping key.

``yaml.safe_load`` keeps the last value for a repeated key, so a second entry silently
replaces the first. ``safe_load`` here is a drop-in that raises instead.
"""

from __future__ import annotations

from typing import IO, Any

import yaml

_MERGE_TAG = "tag:yaml.org,2002:merge"
_VALUE_TAG = "tag:yaml.org,2002:value"


class UniqueKeyLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` that raises ``ConstructorError`` on a repeated mapping key.

    Keys compare as constructed values, so ``1`` and ``true`` repeat each other: the
    resulting dict could hold only one. A key brought in by a ``<<`` merge may be overridden.
    """

    def construct_document(self, node: yaml.Node) -> Any:
        self._reject_repeated_keys(node)
        return super().construct_document(node)

    def _reject_repeated_keys(self, root: yaml.Node) -> None:
        # Before construction: SafeConstructor flattens merges into node.value in place,
        # after which merged-in keys are indistinguishable from the mapping's own.
        visited: set[int] = set()
        stack = [root]
        while stack:
            node = stack.pop()
            if id(node) in visited:
                continue
            visited.add(id(node))
            if isinstance(node, yaml.SequenceNode):
                stack.extend(node.value)
            elif isinstance(node, yaml.MappingNode):
                keys: set[Any] = set()
                for key_node, value_node in node.value:
                    stack.append(value_node)
                    if key_node.tag == _MERGE_TAG or not isinstance(key_node, yaml.ScalarNode):
                        stack.append(key_node)
                        continue
                    # A plain `=` key resolves to the value tag, which flattening later re-tags as str.
                    if key_node.tag == _VALUE_TAG:
                        key = key_node.value
                    else:
                        key = self.construct_object(key_node)
                    if key in keys:
                        raise yaml.constructor.ConstructorError(
                            "while constructing a mapping",
                            node.start_mark,
                            f"found duplicate key {key!r}",
                            key_node.start_mark,
                        )
                    keys.add(key)


def safe_load(stream: str | bytes | IO[str] | IO[bytes]) -> Any:
    """``yaml.safe_load`` that raises ``yaml.constructor.ConstructorError`` on a repeated key."""
    return yaml.load(stream, Loader=UniqueKeyLoader)
