Open Container Interface Support
================================

The OCI Layer
_____________

.. automodule:: carthage.oci
    :members:

    The OCI module contains abstract interfaces for dealing with the container ecosystem.  :class:`OciImage` represents an image that can be pushed to a registry.  :class:`OciContainer` represents a single container.  While an *OciContainer* is a :class:`~Machine`, and a full OS environment including ``ssh`` can run on such a container, that is atypical.  Instead it is more common to set ``oci_command`` and run a single application.

Podman
______

.. automodule:: carthage.podman
    :members:

    Podman is a container frontend that will realize OCI containers on a single system.  Currently Carthage only supports Podman running on the local system through :class:`LocalPodmanContainerHost`.  Long term, support is expected for remote Podman as well.

    
