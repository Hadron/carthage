import typing



def combine_mro(
        base: typing.Union[type, typing.Sequence[type]],
        subclass: type, attribute: str,
        add: typing.Callable,
        state):
    # for all members of the mro of base that are subclasses of *subclass*,  run ``add(mro_member, getattr(mro_member, attribute), state)``
# *base* may be a sequence
    if isinstance(base, type):
        base = [base]
        mro_set = set()
        mro = []
        for b in base:
            if b not in mro_set and issubclass(b, subclass):
                mro_set.add(b)
                mro.append(b)
                try:
                    res = getattr(b, attribute)
                    add(b, res, state)
                except AttributeError: pass
            for m in b.__mro__:
                if m in mro_set: continue
                if not issubclass(m, subclass): continue
                mro_set.add(m)
                mro.append(m)
                try:
                    res = getattr(m, attribute)
                except AttributeError: continue
                add(m, res, state)

def combine_mro_list(base, subclass, attribute):
    def add(m: type, res: list, state):
        for l in res:
            if l not in state:
                state.append(l)
    state = []
    combine_mro(base, subclass, attribute, add, state)
    return state

def combine_mro_mapping(base, subclass, attribute)-> typing.Dict[str, typing.Any]:
    def add(m, res, state) :
        for k,v in res.items():
            if k not in state:
                state[k] = v
    state: typing.Dict[str, typing.Any] = {}
    combine_mro(base, subclass, attribute, add, state)
    return state

__all__ = [
    'combine_mro_list',
    'combine_mro_mapping'
    ]

def setattr_default(obj, a:str, default, inherited_ok = False):
    if inherited_ok:
        has_attr =  hasattr(obj,a)
    else: has_attr = a in obj.__dict__
    if not has_attr:
        setattr(obj, a, default)

__all__ += ['setattr_default']

def gather_from_class(self, *keys, mangle_name = True):
    '''
    :param mangle_name: If true, and name is not in class, set name from __name__
    '''

    d: dict = {}
    if isinstance(self, type):
        cls = self
    else: cls = self.__class__
    for k in keys:
        try: d[k] = getattr(cls, k)
        except AttributeError:
            if k == 'name' and mangle_name:
                d['name'] = cls.__name__.lower()
    return d

__all__ += ['gather_from_class']

def key_from_injector_access(*accesses):
    from .decorators import injector_access
    result = []
    for k in accesses:
        if isinstance(k, injector_access):
            k = k.key
        result.append(k)
    return result

__all__ += ['key_from_injector_access']
