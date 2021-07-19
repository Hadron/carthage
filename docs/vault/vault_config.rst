.. _vault:config:

Configuring Vault
=================

The :meth:`.Vault.apply_configuration()` method will apply a  set of configurations to a Hashicorp Vault.  The intent is to allow initial configuration of policies and authentication.

The method takes a dictionary, but typically this dictionary comes from a YAML file.  The following special keys are recognized:

policy:
    A Dictionary mapping policies to HCL documents.

auth:
    A dictionary for auth method configuration.  The keys in this dictionary are paths on which authentication methods are mounted;  The values are dictionaries containing the following keys:

    type
      The type of authentication method; ``cert``, or ``github`` for example.

    default_lease_ttl
      The default lease TTL

    maximum_lease_ttl
      The longest lived tokens issued by this auth backend.


All other keys in the configuration dictionary will be taken as paths that will be written.  The value will be JSON encoded.  An example YAML file might look like::

  policy:
    sec_admin: |
      path "/sys/policy/*" {
        capabilities = ["read", "update", "create", "delete", "list"]
        }
  auth:
    cert:
      type: cert
      default_lease_ttl: 60m
  auth/cert/cert/hadron:
    certificate: |
      A PEM encoded certificate goes here
    allowed_common_names: ["*@hadronindustries.com"]
