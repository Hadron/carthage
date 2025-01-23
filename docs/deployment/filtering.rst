.. _deployables:filtering:
Filtering Deployables
=====================

.. py:mod:: carthage.deployment

Functions like :func:`run_deployment` default to acting on all the deployables they encounter. Similarly the :ref:`Deployment CLI <deployment:cli>` defaults to acting on all objects.
This may not be desirable:

* Often production changes need to be limited to a small number of machines.

* Layouts may run with different privileges. For example some :class:`deployables <Deployable>` can be deployed only with local credentials stored on a container host or hypervisor. Other deployments require privileges to issue certificates or manipulate more global infrastructure.

* Updating container hosts and hypervisors is often separated from updating containers and VMs even though it is desirable for layouts to model both.

In order to meet these use cases Carthage provides mechanisms to adjust what :class:`deployables <Deployable>` the Deployment Engine works on in several **filter phases**:

#.        For each :class:`Deployable`, the deployment engine instantiates :data:`auto_deploy_policy`. If this key does not exist in the injector hierarchy or if instantiating this key returns truthy, then the object is deployed by default.  Otherwise the object is not deployed by default.
          

#. Functions such as :func:`run_deployment` or :func:`run_deployment_destroy` take a *filter* argument that can make per-call adjustments.


Name Filters
************

:func:`deployable_name_filter` provides an
interface to generate a filter based on the names of *Deployables*.
This mechanism is used by the ``--include`` and ``--exclude`` options
of deployment related command line operations.  For example,
``carthage-runner deploy --include *.example.com`` will include all
objects whose name ends with ``example.com``.

The argument to a name filter consists of:

* An optional name prefix such as ``Machine`` followed by a colon (``:``)

* A string to match. The string may include the ``\*`` wildcard which matches zero or more characters and the ``?`` wildcard which matches exactly one character. Characters with special meanings may be quoted using a leading backslash (``\\``).

:func:`deployable_name_filter` takes a set of include filters (specified on the command line by the ``--include`` argument or by positional arguments) and an exclude filter (specified on the command line by the ``--exclude`` argument).

If only an exclude filter is specified, then all objects that would be deployed by default are deployed, excluding those matched by the include filter.

If only an include filter is specified, then only objects included by the include filter (regardless of whether they would be deployed by default) will be included in the deployment.

If both filters are specified, then the exclude filter takes priority over the include filter.

Deployable Names
****************

The :class:`Deployable` protocol includes a method :meth:`~Deployable.deployable_names` which returns a list of names for a *Deployable*.
Names take the form of a name prefix such as ``Machine``, followed by a colon, followed by  a name in the namespace defined by the prefix.

Filter Outcomes
***************


A filter may return:

``True``
    The Deployable is deployed.

``False``
    The Deployable is not deployed.

``None``
    The auto_deploy_policy is respected.

Example Usage
*************

The following prevents production objects from being deployed unless they are explicitly included::

  class production(Enclave):
    add_provider(auto_deploy_policy, False)
    class prod_1(MachineModel):
      ...


The following permits a complex function to decide whether an object should be deployed by default::

  @inject(ainjector=AsyncInjector)
  async def can_we_get_certificates(ainjector):
      try: await ainjector.get_instance_async(carthage.pki.PkiManager)
          return True
      else: return False

  class layout(CarthageLayout):
      add_provider(auto_deploy_policy, can_we_get_certificates, allow_multiple=True)

because *allow_multiple* is True, the function will be evaluated for each Deployable separately (assuming no deployables are contained in other Deployables). As a result, if some Deployables have access to a *PkiManager*, they will be deployed, while other deployables that do not have access to a *PkiManager* will not be deployed.
Similar logic could be used to exclude Deployables based on availability of other credentials or of configuration state.

