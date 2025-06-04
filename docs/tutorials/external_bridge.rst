Creating an External Bridge
===========================

Often carthage is used to create a layout of related machines that eventually need to connect to a broader network.  Two mechanisms are used:

* The default networking provided by :class:`OCI Containers <carthage.oci.OciContainer` and implementations such as *Podman*.

  * An :data:external network <carthage.network.external_network_key` that creates a bridge The assumption is any :class:`carthage.Machine` placed on this network will be able to DHCP for an address.

    This tutorial focuses on the external network. By default, Carthage models the external network as a bridge  whose name is given by the ``external_bridge_name`` config key.

    So, for example if ``/etc/carthage_system.conf`` (the default system configuration file) contains:

.. code:: yaml

   external_bridge_name: blaptop

Then Carthage will assume the existence of a bridge called ``blaptop`` that serves dhcp to any machine connected to it.

Creating the Bridge Device
**************************

To create a bridge device called ``blaptop``, create a file ``/etc/systemd/network/blaptop.netdev`` with the following contents::

  [NetDev]
  Name=blaptop
  Kind=bridge
  [Bridge]
  VLANFiltering=False

Configuring the Bridge
**********************

To provide networking for the bridge, create a file ``/etc/systemd/network/blaptop.network`` with the following contents::

  [Match]
  Name=blaptop
  [Network]

  Address=10.38.0.1/24
  IPMasquerade=yes
  DHCPServer=yes

Then run ``systemctl restart systemd-networkd`` to create and configure the bridge.

Configuring Carthage
********************

In general attaching to a bridge requires root. Configuring qemu or other Machine implementations to allow using a bridge as non-root is beyond this tutorial.

To configure Carthage running as root to use ``blaptop``, create or edit ``/etc/carthage_system.conf``.  This is a *YAML* file.  Include the following key:

.. code:: yaml

   external_bridge_name: blaptop

   
