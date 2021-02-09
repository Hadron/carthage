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
