Carthage
========

Carthage is an **Infrastructure as Code (IAC)** framework.
Carthage provides models for infrastructure concepts such as machines, networks, and domains or groups of machines.  There are concrete implementations of these models including containers and virtual machines.

Carthage allows experts to quickly construct infrastructure from a Carthage layout.  Infrastructure can be real, virtual, or a mixture.  Often the same layout is used to produce both real and virtual infrastructure.

Examples of applications written using Carthage include:

* A test framework to run tests against a distributed product by replicating virtual network infrastructure and running tests against that structure.

* A cyber training application to produce training environments with thousands of virtual machines and realistic training environments including firewalls, routers, and Internet services.  The training environment is entirely isolated from the Internet.

* A devops application to maintain the real infrastructure on which the cyber training environment runs.

Installing Carthage
*******************

Carthage typically requires  a container environment as well as a virtualization requirement.  On Debian or Ubuntu systems, install the following::

  apt install socat systemd-container libvirt-clients qemu-utils bridge-utils fai-setup-storage
  apt install --no-install-recommends fai-server dosfstools
  
You may also want either ``python3-pyvmomi`` or ``libvirt-daemon-system``.

Then install Carthage like any other source distribution, perhaps using ``python3 setup.py install`` or ``python3 setup.py install --user``.

Alternatively you can ``podman pull ghcr.io/hadron/carthage:latest``

Learning Carthage
*****************
Documentation is available `here <https://carthage.readthedocs.io/>`_.


For a look at a sample application take a look at https://github.com/hartmans/industrial-algebra.carthage .  This application configures the laptop of one of the Carthage developers.

Join Us
*******

As you start to use Carthage, you will probably have questions.  The best approach is to discuss them in real time on our  `Matrix chat <https://matrix.to/#/#carthage-users:matrix.org>`.
