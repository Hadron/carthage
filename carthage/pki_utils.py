# Copyright (C)2025,  Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import datetime
import re
import warnings

try:
    import cryptography
    import cryptography.x509
    from cryptography.hazmat.backends import default_backend
except ImportError:
    cryptography = None
    issued_no_crypto_warning = False
            

__all__ = []

def x509_annotate_str(pem_certificate:str, /):
    '''
    Add subject, issuer and expiration information
    '''
    if not cryptography:
        return certificate
    cert = cryptography.x509.load_pem_x509_certificate(pem_certificate.encode('utf-8'), default_backend())
    s = ''
    s += 'Subject: {}\n'.format(', '.join([ '{}={}'.format(x.oid._name, x.value) for x in cert.subject ])) 
    s += 'Issuer: {}\n'.format(', '.join([ '{}={}'.format(x.oid._name, x.value) for x in cert.issuer ])) 
    s += 'Not Valid Before: {}\n'.format(cert.not_valid_before_utc)
    s += 'Not Valid After: {}\n'.format(cert.not_valid_after_utc)
    return s+pem_certificate

__all__ += ['x509_annotate_str']

def x509_annotate(pem_in:str|bytes, /):
    '''
    Annotate a certificate chain (multiple certificates) with expiration, subject, and issuer
    '''
    if isinstance(pem_in, bytes):
        s = str(pem_in, 'ascii')
    else:
        s = pem_in
    bstr = '^-----BEGIN CERTIFICATE-----$'
    estr = '^-----END CERTIFICATE-----$'

    ret = ''

    while True:

        m = re.search(f'{bstr}[^-]+{estr}', s, re.MULTILINE)
        if not m:
            break
        ret = ret + x509_annotate_str(m.group(0))
        s = s[m.end(0):]

    return ret

__all__ += ['x509_annotate']


def certificate_is_expired(pem_str:str,/, days_left=None, fraction_left=None):
    '''
    Returns whether a certificate is expired.
    :param days_left: Consider a certificate expired if There are les than *days_left* remaining
    :param fraction_left: Consider a certificate expired if Less than the given fraction of time is still available.

    Certificates are always considered expired if they are actually expired (no time is left).
    '''
    
    global issued_no_crypto_warning
    if not cryptography:
        if not issued_no_crypto_warning:
            warnings.warn('python-cryptography is not available; certificates assumed not to be expired')
            issued_no_crypto_warning = True
        return False
    cert = cryptography.x509.load_pem_x509_certificate(pem_str.encode('utf-8'), default_backend())
    total_delta = cert.not_valid_after_utc-cert.not_valid_before_utc
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    until_expire = now-cert.not_valid_after_utc
    if until_expire.seconds <= 0:
        return True
    if days_left is not None and until_expire.days < days_left:
        return True
    if fraction_left is not None and until_expire.seconds < total_delta.seconds*fraction_left:
        return True
    return False

__all__ += ['certificate_is_expired']
