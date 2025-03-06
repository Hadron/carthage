# Copyright (C) 2018, 2020, 2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import contextlib
import pathlib
import os
import os.path
import re
from .dependency_injection import *
from . import sh
from .config import ConfigLayout
from .utils import memoproperty
from . import machine
from .setup_tasks import *

from .pki_utils import *

RESOURCES_DIR = pathlib.Path(__file__).parent.joinpath('resources')

__all__ = []
def split_pem(s):
    '''
     An iterator that splits PEM encapsulated objects, yielding one object for each PEM begin and end boundary string.
     '''
    bstr = '-----BEGIN CERTIFICATE-----\n'
    estr = '-----END CERTIFICATE-----\n'
    while True:
        m = re.search(f'{bstr}[^-]+{estr}', s)
        if not m:
            break
        yield x509_annotate_str(m.group(0))
        s = s[m.end(0):]

@inject(injector=Injector)
def ca_file(certificates, *,
            name=None,
            injector):
    '''
    requests and other Python constructs want a file containing one or more PEM encoded certificates to use as a CA file (trust stores and intermediates that are not sent by clients).
    This function generates such a file.

    :param certificates: A string or list of PEM encoded certificates to be used as trust roots.

    :param name: If specified,   a name for this trust store that will be part of the file name.

    '''
    assert name, 'Currently support for name=None is unimplemented; probably the right approach is to hash the certificates.'
    config = injector(ConfigLayout)
    cache_dir = pathlib.Path(config.cache_dir)
    truststores = cache_dir/'truststores'
    truststores.mkdir(parents=True, exist_ok=True)
    name = name.replace('/', '_')
    ca_path = truststores/name
    if isinstance(certificates, str):
        certificates = list(split_pem(certificates))
    with ca_path.open('wt') as ca_file:
        for certificate in certificates:
            ca_file.write(certificate)
    return ca_path

__all__ += ['ca_file']

class PkiManager(SetupTaskMixin, AsyncInjectable):

    '''
    Represents a source of certificates.
    The PKIManager is responsible for generating the key, any necessary CSR, and signing the certificate.
    A key should be used for a single use (installed onto a single system). If an edge certificate is being reused for multiple nodes in a cluster, a layer above PkiManager should handle that reuse.

    Adding additional reporting and query interfaces in subclasses to be used in debugging contexts often makes sense. Similarly there is a role for interfaces to set up subordinate CAs or to provide for CA rotation.

    Relying on additional interfaces in setup_tasks or roles that requests certificates **strongly discouraged**. Any such reliance limits the sets of credential sources that can be used for a given role.

    '''

    async def issue_credentials(self, hostname: str, tag: str) -> list[str, str]:
        '''Issue a set of credentials for a given host.

        :param host: the hostname to use as the CN and in a DNS SAN.

        :param tag: A tag that describes the context in which a given
        credential is requested. For example this could include the
        name of the setup_task and name of the
        :class:`carthage.Machine` that credentials are being installed
        on. It is an error to request credentials for the same
        hostname and for different tags within the same invocation of
        Carthage. PkiManagers should return different keys for
        situations when different tags are used across Carthage
        runs. (Return different keys all the time is even better.

        :returns: key, certificate

        '''
        raise NotImplementedError

    async def trust_store(self) -> 'TrustStore':
        '''
        :returns: A :class:`TrustStore` containing trust roots for accessing entities certified by this PkiManager.
        '''
        raise NotImplementedError

    async def certificates(self, include_expired=True):
        '''
        :returns: iterator  of all certificates (as PEM strings) issued by this PkiManager. If *include_expired* is False, the PkiManager may (but need not) exclude expired certificates.
        '''
        raise NotImplementedError

    async def issue_credentials_onefile(self, hostname, tag):
        '''Like issue_credentials, but combine key and cert into one string.
        '''
        key, cert = await self.issue_credentials(hostname, tag)
        return key+'\n'+cert
    
__all__ += ['PkiManager']


class EntanglementPkiManager(PkiManager):

    '''
    A :class:`PkiManager` based on the ``entanglement-pki`` command.
    This is a very simple PkiManager that effectively just wrapts ``openssl req`` and ``openssl x509``.
    It is suitable for testing  but not typically for production deployments.
   '''

    async def issue_credentials(self, hostname:str, tag:str) -> list[str, str]:
        current_tags = self.tags_by_hostname.setdefault(hostname, set())
        if tag in current_tags:
            raise RuntimeError(f'{hostname} credentials already retrieved with {tag}')
        current_tags.add(tag)
        self._certify(hostname)
        self.tags_by_hostname[hostname] = current_tags
        key_path = self.pki_dir/f'{hostname}.key'
        cert_path = self.pki_dir/f'{hostname}.pem'
        cert = cert_path.read_text()
        cert = x509_annotate(cert)
        return key_path.read_text(), cert

    async def certificates(self, include_expired=True):
        for cert in self.pki_dir.glob('*.pem'):
            yield x509_annotate(cert.read_text())

    async def trust_store(self):
        return await self.ainjector(
            SimpleTrustStore,
            'carthage_root',
            {'carthage_root': self.ca_cert})
        
    def _certify(self, host):
        self.ca_cert
        sh.entanglement_pki('--force', host, d=str(self.pki_dir), _bg=False)

    @memoproperty
    def pki_dir(self):
        ret = pathlib.Path(self.config_layout.state_dir)/"pki"
        ret.mkdir(exist_ok=True, parents=True, mode=0o700)
        return ret

    @property
    def ca_cert(self):
        ca_path = self.pki_dir/'ca.pem'
        try:
            if certificate_is_expired(ca_path.read_text(), fraction_left=0.33):
                ca_path.unlink()
        except FileNotFoundError: pass
        sh.entanglement_pki('-d', self.pki_dir,
                            '--ca-name', "Carthage Root CA", _bg=False)
        return ca_path.read_text()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config_layout = self.injector(ConfigLayout)
        self.tags_by_hostname = {}
        
__all__ += ['EntanglementPkiManager']

class TrustStore(AsyncInjectable):
    

    '''
    Represents a set of trust roots for a given certificate
    authority or set of certificate authorities. It is acceptable to
    also include intermediate certificates in the trust store, so long
    as it would be appropriate for a relying party to trust those keys
    as trust roots. Some relying parties will treat intermediate certificates as roots, some will simply use them as additional certificates in validation.
    '''

    #: a name of this trust store. Trust stores with a different set of trusted certificates must have different names.
    name: str
    _ca_file = None

    async def trusted_certificates(self):
        '''
        An asynchronous iterator yielding pairs of anchor_name, certificate. The anchor_name is used by interfaces like ``ca-certificates`` that need to name each trust root. If the underlying store does not have anchor names, hashes can be used.
        '''
        raise NotImplementedError

    async def ca_file(self):
        '''
        Produce a CA file suitable for input to requests.

        '''
        # This routine assumes we are going to cache the CA file. So,
        # if called multiple times it will give the same results even
        # if self.certificates() changes. To support changes in
        # self.certificates() we would need to be clever enough to use
        # different ca_files for different calls. We do not want to
        # rewrite ca_file while it may be used in another thread. If
        # :func:`ca_file` gains support for hashing certificates, we
        # could use that support to deal with self.certificates()
        # changing.
        if self._ca_file:
            return self._ca_file
        certificates = [cert async for anchor_name, cert in self.trusted_certificates()]
        self._ca_file = self.injector(ca_file, certificates, name=self.name)
        return self._ca_file

    def __init__(self, name, **kwargs):
        super().__init__(**kwargs)
        self.name = name

    def __repr__(self):
        return f'<{self.__class__.__name__} {self.name}>'

__all__ += ['TrustStore']

#: The trust store for using when contacting some API service, instantiated on the injector of the service being contacted.
contact_trust_store_key = InjectionKey(TrustStore, role='contact')

__all__ += ['contact_trust_store_key']

#: The key for a trust store for validating client certificates; always optional
client_certificate_trust_store_key = InjectionKey(TrustStore, role='client_certificate', _optional=True)

__all__ += ['client_certificate_trust_store_key']

#: The organization's local trust roots (inherently optional)
trust_roots_key = InjectionKey(TrustStore, role='organization_trust_roots', _optional=True)

__all__ += ['trust_roots_key']

def install_root_cert_customization(get_certificate_info):
    '''
    Return a customization that installs one or more root certificates.

    :param get_get_certificate_info: A callback function resolved in the context of the customization's :class:`AsyncInjector` that returns a sequence of [certificate_name, pem_certificate].  The *certificate_name* uniquely identifies the certificate and *pem_certificate* is the root certificate to add.

    '''
    class InstallRootCertCustomization(machine.FilesystemCustomization):

        @setup_task("Install  Root Certs")
        async def install_root_cert(self):
            certificates_to_install = await self.ainjector(get_certificate_info)
            carthage_cert_dir = os.path.join(
                    self.path,
                    "usr/share/ca-certificates/carthage")
            os.makedirs(carthage_cert_dir, exist_ok=True)
            for name, pem_cert in certificates_to_install.items():
                with open(os.path.join(
                        carthage_cert_dir, f"{name}.crt"),
                          "wt") as f:
                    f.write(pem_cert)
                with open(os.path.join(
                        self.path, "etc/ca-certificates.conf"),
                          "ta") as f:
                    f.write(f"carthage/{name}.crt\n")
            await self.run_command("/usr/sbin/update-ca-certificates")

    return InstallRootCertCustomization

__all__ += ['install_root_cert_customization']

@inject(
    pki=InjectionKey(PkiManager, _optional=True),
    trust_roots=trust_roots_key)
async def pki_manager_certificate_info(pki, trust_roots):
    '''
    Returns two sets of potential trust roots:

    * PkiManager.trust_store.trusted_certificates: the trust roots necessary to trust certificates issued by the PkiManager Carthage is using

    * trust_roots_key.trusted_certificates: the trust roots local to the current organization.

    These trust roots may be overlapping.  Duplicate tags are suppressed.  For example if the PkiManager is using the local organization's CA, the trust roots may be identical.

    '''
    certificates_to_install = {}
    if pki:
        pki_trust_store = await pki.trust_store()
    else:
        pki_trust_store = None
    for store in (trust_roots, pki_trust_store):
        if store is None: continue
        async for tag, cert in store.trusted_certificates():
            if tag in certificates_to_install: continue
            certificates_to_install[tag] = cert
    return certificates_to_install
    return [('carthage_pki', pki.ca_cert)]

PkiCustomizations = install_root_cert_customization(pki_manager_certificate_info)

__all__ += ["PkiCustomizations"]


class SimpleTrustStore(TrustStore):

    '''
    A :class:`TrustStore` with a constant set of trust roots specified at class initialization time.
    '''

    def __init__(self, name, trust_roots, **kwargs):
        '''
        :param trust_roots: a dictionary mapping anchor_names to single certificates.
        '''
        self.trust_roots = trust_roots
        super().__init__(name=name, **kwargs)

    async def trusted_certificates(self):
        for anchor_name, cert in self.trust_roots.items():
            yield anchor_name, cert

__all__ += ['SimpleTrustStore']

@inject(
    production=InjectionKey('certbot_production_certificates', _optional=NotPresent))
class LetsencryptTrustStore(TrustStore):

    '''
    A trust store for certificates obtained from letsencrypt.
contains either the LE staging roots or the LE production roots.
    '''

    production:bool = True

    def __init__(self, **kwargs):
        # This complexity is to figure out the name before calling super.
        if 'production' in kwargs:
            self.production = kwargs.pop('production')
        stem = 'production' if self.production else 'staging'
        name = 'letsencrypt-'+stem
        super().__init__(name=name, **kwargs)
        
    async def trusted_certificates(self):
        stem = 'production' if self.production else 'staging'
        truststore = RESOURCES_DIR/f'letsencrypt-{stem}.pem'
        i = 0
        for cert in split_pem(truststore.read_text()):
            yield 'letsencrypt-staging-'+str(i), cert
            i += 1
            

__all__ += ['LetsencryptTrustStore']

class PemBundleTrustStore(TrustStore):

    '''
    A trust store that takes certificates from a CA file containing multiple certificates in a file.

    :param name: The name of the trust store

    :param pem_bundle: a file containing one or more PEM certificates.

    '''

    def __init__(self, name,pem_bundle, **kwargs):
        super().__init__(name=name, **kwargs)
        self.pem_bundle = pathlib.Path(pem_bundle)

    async def trusted_certificates(self):
        counter = 1
        for cert in split_pem(self.pem_bundle.read_text()):
            try:
                cert = x509_modify(cert)
            except (ImportError, NameError): pass
            yield f'{self.name}_{counter}', cert
            counter += 1

__all__ += ['PemBundleTrustStore']
