from copy import copy
from enum import (
    auto,
    Enum,
)
from typing import (
    Any,
    List,
    Tuple,
)

from tri_declarative import (
    declarative,
    dispatch,
    getattr_path,
    Namespace,
    Refinable,
    refinable,
)
from tri_declarative.refinable import is_refinable_function

from iommi.base import items


def prefixes(path):
    parts = [p for p in path.split('__') if p]
    for i in range(len(parts)):
        yield '__'.join(parts[: i + 1])


class Prio(Enum):
    refine_defaults = auto()
    table_defaults = auto()
    member_defaults = auto()
    constructor = auto()
    shortcut = auto()
    style = auto()
    base = auto()
    member = auto()
    refine = auto()


def flatten_items(namespace: Namespace, _prefix: str = '') -> List[Tuple[str, Any]]:
    for key, value in dict.items(namespace):
        path = _prefix + key
        if isinstance(value, Namespace):
            if value:
                yield from flatten_items(value, _prefix=path + '__')
            else:
                yield path, value
        else:
            yield path, value


class RefinableNamespace(Namespace):
    __iommi_refined_stack: List[Tuple[Prio, Namespace, List[Tuple[str, Any]]]]

    def as_stack(self):
        return [(prio.name, dict(flattened_params)) for prio, _, flattened_params in self._get_parent_stack()]

    def _get_parent_stack(self) -> List[Tuple[Prio, Namespace, List[Tuple[str, Any]]]]:
        try:
            return object.__getattribute__(self, '__iommi_refined_stack')
        except AttributeError:
            return [
                (Prio.base, self, list(flatten_items(self))),
            ]

    def _refine(self, prio: Prio, **kwargs) -> 'RefinableNamespace':
        params = Namespace(**kwargs)

        stack = self._get_parent_stack() + [
            (prio, params, list(flatten_items(params))),
        ]
        stack.sort(key=lambda x: x[0].value)

        result = RefinableNamespace()
        object.__setattr__(result, '__iommi_refined_stack', stack)

        missing = object()

        for prio, params, flattened_params in stack:
            for path, value in flattened_params:
                found = False
                for prefix in prefixes(path):
                    existing = getattr_path(result, prefix, missing)
                    if existing is missing:
                        break
                    new_updates = getattr_path(params, prefix)

                    if isinstance(existing, RefinableObject):
                        if isinstance(new_updates, dict):
                            existing = existing.refine(prio, **new_updates)
                        else:
                            existing = new_updates
                        result.setitem_path(prefix, existing)
                        found = True

                    if isinstance(new_updates, RefinableObject):
                        result.setitem_path(prefix, new_updates)
                        found = True

                if not found:
                    result.setitem_path(path, value)

        return result


class EvaluatedRefinable(Refinable):
    pass


def evaluated_refinable(f):
    f = refinable(f)
    f.__iommi__evaluated = True
    return f


def is_evaluated_refinable(x):
    return isinstance(x, EvaluatedRefinable) or getattr(x, '__iommi__evaluated', False)


class RefinableMembers(Refinable):
    pass


@declarative(
    member_class=Refinable,
    parameter='refinable',
    is_member=is_refinable_function,
    add_init_kwargs=False,
)
class RefinableObject:
    iommi_namespace: RefinableNamespace
    is_refine_done: bool

    @dispatch
    def __init__(self, **kwargs):
        self.iommi_namespace = RefinableNamespace(**kwargs)
        declared_items = self.get_declared('refinable')
        unknown_args = [name for name in kwargs if name not in declared_items]
        if unknown_args:
            available_keys = '\n    '.join(sorted(declared_items.keys()))
            raise TypeError(
                self.__class__.__name__
                + ' object has no refinable attribute(s): '
                + ', '.join(f'"{k}"' for k in sorted(unknown_args))
                + '.\n'
                + 'Available attributes:\n'
                + f'    {available_keys}\n'
            )

        self.is_refine_done = False

    def refine_done(self, parent=None):
        result = copy(self)
        del self

        assert not result.is_refine_done, f"refine_done() already invoked on {result!r}"

        if hasattr(result, 'apply_styles'):
            is_root = parent is None
            if is_root:
                result._iommi_style_stack = []
            else:
                result._iommi_style_stack = list(parent._iommi_style_stack)
            iommi_style = result.iommi_namespace.get('iommi_style')

            from iommi.style import resolve_style

            iommi_style = resolve_style(result._iommi_style_stack, iommi_style)
            result._iommi_style_stack += [iommi_style]
            result = result.apply_styles(result._iommi_style_stack[-1], is_root=is_root)

        # Apply config from result.namespace to result
        declared_items = result.get_declared('refinable')
        remaining_namespace = dict(result.iommi_namespace)
        for k, v in items(declared_items):
            if k == 'iommi_style':
                remaining_namespace.pop(k, None)
                continue
            if isinstance(v, Refinable):
                setattr(result, k, remaining_namespace.pop(k, None))
            else:
                if k in remaining_namespace:
                    setattr(result, k, remaining_namespace.pop(k))

        if remaining_namespace:
            unknown_args = list(remaining_namespace.keys())
            available_keys = '\n    '.join(sorted(declared_items.keys()))
            raise TypeError(
                result.__class__.__name__
                + ' object has no refinable attribute(s): '
                + ', '.join(f'"{k}"' for k in sorted(unknown_args))
                + '.\n'
                + 'Available attributes:\n'
                + f'    {available_keys}\n'
            )
        result.is_refine_done = True

        result.on_refine_done()

        return result

    def on_refine_done(self):
        pass

    def refine(self, prio: Prio = Prio.refine, **args):
        assert not self.is_refine_done, f"Already called refine_done on {self!r}"
        if prio == Prio.constructor:
            # Inplace
            result = self
        else:
            result = copy(self)

        result.iommi_namespace = self.iommi_namespace._refine(prio, **args)

        return result

    def refine_defaults(self, **args):
        return self.refine(Prio.refine_defaults, **args)

    def __repr__(self):
        return (
            f"<{self.__class__.__name__} " + ' '.join(f'{k}={v}' for k, v in flatten_items(self.iommi_namespace)) + ">"
        )
