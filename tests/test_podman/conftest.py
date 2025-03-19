# Copyright (C)  2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import pytest
from carthage.podman.container_host import LocalPodmanSocket, LocalPodmanContainerHost
import sh # Not Carthage.sh


@pytest.fixture(scope='module')
def container_host_fixture(request):
    match request.param:
        case 'remote':
            from .podman_remote_host import container_host
            return container_host
        case 'local':
            return LocalPodmanContainerHost
        case 'local_socket':
            try:
                sh.podman('--remote', 'info')
            except sh.ErrorReturnCode_125:
                pytest.skip('podman socket not available')
            return LocalPodmanSocket
        
            

def pytest_generate_tests(metafunc):
    if 'container_host_fixture' in metafunc.fixturenames:
        if metafunc.config.getoption('remote_container_host'):
            metafunc.parametrize('container_host_fixture', ['remote'], indirect=True)
        else:
            metafunc.parametrize('container_host_fixture', ['local', 'local_socket'], indirect=True)
            
