from collections import OrderedDict
import functools
from functools import total_ordering, wraps
import inspect
import itertools

from tri.struct import Struct


__version__ = '0.12.1'


def with_meta(class_to_decorate=None, add_init_kwargs=True):
    """
    Class decorator to enable a class (and it's sub-classes) to have a 'Meta' class attribute.
    """

    if class_to_decorate is None:
        return functools.partial(with_meta, add_init_kwargs=add_init_kwargs)

    if add_init_kwargs:
        def get_extra_args_function(self):
            return {k: v for k, v in self.get_meta().items() if not k.startswith('_')}
        add_args_to_init_call(class_to_decorate, get_extra_args_function)

    setattr(class_to_decorate, 'get_meta', classmethod(get_meta))

    return class_to_decorate


def get_meta(cls):
    """
        Collect all members of any contained :code:`Meta` class declarations from the given class or any of its base classes.
        (Sub class values take precedence.)
    """
    merged_attributes = Struct()
    for class_ in reversed(cls.mro()):
        if hasattr(class_, 'Meta'):
            for key, value in class_.Meta.__dict__.items():
                if key.startswith('__'):  # Skip internal attributes
                    continue
                merged_attributes[key] = value
    return merged_attributes


def creation_ordered(class_to_decorate):
    """
        Class decorator that ensures that instances will be ordered after creation order when sorted.
    """

    next_index = functools.partial(next, itertools.count())

    __init__orig = class_to_decorate.__init__

    def __init__(self, *args, **kwargs):
        object.__setattr__(self, '_index', next_index())
        __init__orig(self, *args, **kwargs)

    setattr(class_to_decorate, '__init__', __init__)

    # noinspection PyProtectedMember
    def __lt__(self, other):
        return self._index < other._index

    setattr(class_to_decorate, '__lt__', __lt__)

    class_to_decorate = total_ordering(class_to_decorate)

    return class_to_decorate


def declarative(member_class, parameter='members', add_init_kwargs=True):
    """
        Class decorator to enable classes to be defined in the style of django models.
        That is, @declarative classes will get an additional argument to constructor,
        containing an OrderedDict with all class members matching the specified type.
    """

    def get_members(cls):
        members = OrderedDict()
        for base in cls.__bases__:
            inherited_members = getattr(base, '_declarative_' + parameter, {})
            members.update(inherited_members)

        def generate_member_bindings():
            for name, obj in cls.__dict__.items():
                if isinstance(obj, member_class) and not name.startswith('__'):
                    yield name, obj

        members.update(sorted(generate_member_bindings(), key=lambda x: x[1]))

        return members

    def decorator(class_to_decorate):

        class DeclarativeMeta(class_to_decorate.__class__):
            def __init__(cls, name, bases, dict):
                setattr(cls, '_declarative_' + parameter, get_members(cls))
                super(DeclarativeMeta, cls).__init__(name, bases, dict)

        new_class = DeclarativeMeta(class_to_decorate.__name__,
                                    class_to_decorate.__bases__,
                                    {k: v for k, v in class_to_decorate.__dict__.items() if k not in ['__dict__', '__weakref__']})

        if add_init_kwargs:
            def get_extra_args_function(self):
                return {parameter: getattr(self, '_declarative_' + parameter)}
            add_args_to_init_call(new_class, get_extra_args_function)

        setattr(new_class, 'get_declared', classmethod(get_declared))

        return new_class

    return decorator


def get_declared(cls, parameter='members'):
    """
        Get the :code:`OrderedDict` value of the parameter collected by the :code:`@declarative` class decorator.
        This is the same value that would be submitted to the :code:`__init__` invocation in the :code:`members`
        argument (or another name if overridden by the :code:`parameter` specification)
    """

    return getattr(cls, '_declarative_' + parameter)


def add_args_to_init_call(cls, get_extra_args_function):
    __init__orig = object.__getattribute__(cls, '__init__')  # Use object.__getattribute__ to not have the original implementation bind to the class

    pos_arg_names = getattr(__init__orig, 'pos_arg_names', None)
    if pos_arg_names is None:
        try:
            pos_arg_names, _, _, _ = inspect.getargspec(__init__orig)
            pos_arg_names = list(pos_arg_names)[1:]  # Skip 'self'
        except TypeError:
            # We might fail on not being able to find the signature of builtin constructors
            pass

    def __init__(self, *args, **kwargs):
        new_kwargs = {}
        new_kwargs.update(get_extra_args_function(self))
        if pos_arg_names:
            if len(args) > len(pos_arg_names):
                raise TypeError('Too many positional argument')
            new_kwargs.update((k, v) for k, v in zip(pos_arg_names, args))
            args = []
        new_kwargs.update(kwargs)
        __init__orig(self, *args, **new_kwargs)

    __init__.pos_arg_names = pos_arg_names
    setattr(cls, '__init__', __init__)


def get_signature(func):
    """
    :type func: Callable
    """
    try:
        return func.__tri_declarative_signature
    except AttributeError:
        try:
            names, _, _, _ = inspect.getargspec(func)
            func.__tri_declarative_signature = create_signature(names)
        except TypeError:
            return None
        return func.__tri_declarative_signature


def create_signature(names):
    return ','.join(sorted(names))


def should_not_evaluate(f):
    if not callable(f):
        return f

    @wraps(f)
    def wrapper(*args, **kwargs):
        return f(*args, **kwargs)

    wrapper.__tri_declarative_signature = None
    wrapper.__tri_declarative_signature_underlying = get_signature(f)
    return wrapper


# Bypass the should_not_evaluate flag
def should_evaluate(f):
    if not callable(f):
        return f

    @wraps(f)
    def wrapper(*args, **kwargs):
        return f(*args, **kwargs)

    # noinspection PyUnresolvedReferences
    signature = get_signature(f)
    wrapper.__tri_declarative_signature = signature if signature is not None else f.__tri_declarative_signature_underlying
    return wrapper


def force_evaluate(f, **kwargs):
    return evaluate(should_evaluate(f), **kwargs)


def evaluate(func_or_value, signature=None, **kwargs):
    if callable(func_or_value):
        if signature is None:
            signature = create_signature(kwargs)

        if get_signature(func_or_value) == signature:
            return func_or_value(**kwargs)
    return func_or_value


def evaluate_recursive(func_or_value, signature=None, **kwargs):
    if signature is None:
        signature = create_signature(kwargs)

    if isinstance(func_or_value, dict):
        # The type(item)(** stuff is to preserve the original type
        return type(func_or_value)(**{k: evaluate_recursive(v, signature=signature, **kwargs) for k, v in func_or_value.items()})

    if isinstance(func_or_value, list):
        return [evaluate_recursive(v, signature=signature, **kwargs) for v in func_or_value]

    if isinstance(func_or_value, set):
        return {evaluate_recursive(v, signature=signature, **kwargs) for v in func_or_value}

    return evaluate(func_or_value, signature=signature, **kwargs)


def should_show(item):
    try:
        return item.show
    except AttributeError:
        try:
            return item['show']
        except (TypeError, KeyError):
            return True


def filter_show_recursive(item):
    if isinstance(item, list):
        return [filter_show_recursive(v) for v in item if should_show(v)]

    if isinstance(item, set):
        return {filter_show_recursive(v) for v in item if should_show(v)}

    if isinstance(item, dict):
        # The type(item)(** stuff is to preserve the original type
        return type(item)(**{k: filter_show_recursive(v) for k, v in item.items() if should_show(v)})

    return item


def remove_keys_recursive(item, keys_to_remove):
    if isinstance(item, list):
        return [remove_keys_recursive(v, keys_to_remove) for v in item]

    if isinstance(item, set):
        return {remove_keys_recursive(v, keys_to_remove) for v in item}

    if isinstance(item, dict):
        return {k: remove_keys_recursive(v, keys_to_remove) for k, v in item.items() if k not in keys_to_remove}

    return item


def remove_show_recursive(item):
    return remove_keys_recursive(item, {'show'})


def extract_subkeys(kwargs, prefix, defaults=None):
    """
    Extract

    >>> foo = {
    ...     'foo__foo': 1,
    ...     'foo__bar': 2,
    ...     'baz': 3,
    ... }
    >>> assert extract_subkeys(foo, 'foo', defaults={'quux': 4}) == {
    ...     'foo': 1,
    ...     'bar': 2,
    ...     'quux': 4,
    ... }

    @type kwargs: dict
    @return dict
    """

    prefix += '__'
    result = {k[len(prefix):]: v for k, v in kwargs.items() if k.startswith(prefix)}
    if defaults is not None:
        return setdefaults(result, defaults)
    else:
        return result


def setdefaults(d, d2):
    """
    @type d: dict
    @type d2: dict
    @return dict
    """
    for k, v in d2.items():
        d.setdefault(k, v)
    return d


def getattr_path(obj, path):
    """
    Get an attribute path, as defined by a string separated by '__'.
    getattr_path(foo, 'a__b__c') is roughly equivalent to foo.a.b.c but
    will short circuit to return None if something on the path is None.
    """
    path = path.split('__')
    for name in path:
        obj = getattr(obj, name)
        if obj is None:
            return None
    return obj


def setattr_path(obj, path, value):
    """
    Set an attribute path, as defined by a string separated by '__'.
    setattr_path(foo, 'a__b__c', value) is equivalent to "foo.a.b.c = value".
    """
    path = path.split('__')
    o = obj
    for name in path[:-1]:
        o = getattr(o, name)
    setattr(o, path[-1], value)
    return obj


LAST = object()


def sort_after(l):
    to_be_moved_by_index = []
    to_be_moved_by_name = []
    to_be_moved_last = []
    result = []
    for x in l:
        after = getattr(x, 'after', None)
        if after is None:
            result.append(x)
        elif after is LAST:
            to_be_moved_last.append(x)
        elif type(after) == int:
            to_be_moved_by_index.append(x)
        else:
            to_be_moved_by_name.append(x)

    for x in reversed(to_be_moved_by_name):
        for i, y in enumerate(result):
            if y.name == x.after:
                result.insert(i + 1, x)
                break

    for x in reversed(to_be_moved_by_index):
        result.insert(x.after, x)

    result.extend(to_be_moved_last)

    return result
