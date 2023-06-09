import importlib.resources


class ResourceLoader(object):
    SEARCH_PATH = importlib.resources.files("carthage.resources")

    @classmethod
    def load(cls, name):
        for x in cls.SEARCH_PATH.iterdir():
            if name == x.parts[-1]:
                return x.open().read()
        raise FileNotFoundError(f"{name} was not found in {cls.SEARCH_PATH}")


class TemplateLoader(ResourceLoader):
    SEARCH_PATH = importlib.resources.files("carthage.resources.templates")


class SkelLoader(ResourceLoader):
    SEARCH_PATH = importlib.resources.files("carthage.resources.skel")
