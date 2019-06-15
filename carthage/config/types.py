import os.path, re
from ..dependency_injection import inject, InjectionKey
from .layout import ConfigLayout

key_re = re.compile( r'(?<!\$|\\)\{([a-zA-Z0-9_\.]+)\}')

def getattr_path(o, attrs):
    attrs_iter = attrs
    try:
        while attrs_iter:
            left, sep, attrs_iter = attrs_iter.partition('.')
            o = getattr(o, left)
            if not attrs_iter:
                return o
    except AttributeError:
        raise AttributeError(f'Unable to find {attrs}') from None

@inject(config = ConfigLayout)
class ConfigString(str):

    '''A string that substitutes ```{key}``` with the result of that config key
    '''


    def __new__(cls, s, *, config):
        def cb(k):
            return getattr_path(config, k.group(1))
        s = str(s)
        s = key_re.sub(cb, s)
        return str.__new__(str, s)

@inject(config = ConfigLayout)
class ConfigPath(ConfigString):

    def __new__(cls, s, *, config):
        return super().__new__(ConfigString, os.path.expanduser(os.path.expandvars(s)), config = config)
