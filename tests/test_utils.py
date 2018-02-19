from carthage.utils import memoproperty

def test_memo_prop():
    class m:
        @memoproperty
        def foo(self):
            nonlocal called
            assert called is False
            called = True
            return 99
    called = False
    assert isinstance(m.foo, memoproperty)
    mo = m()
    assert mo.foo == 99
    assert mo.foo == 99 #and not called a second time
    
