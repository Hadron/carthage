# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
    assert mo.foo == 99  # and not called a second time
