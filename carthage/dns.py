# Copyright (C) 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio

import logging
from .dependency_injection import AsyncInjectable, inject_autokwargs, InjectionKey, inject, AsyncInjector
from .network import NetworkLink

__all__ = []

logger = logging.getLogger('carthage.network')


class DnsZone(AsyncInjectable):

    def __init__(self, name=None, **kwargs):
        if name:
            self.name = name
        super().__init__(**kwargs)
        if not getattr(self, 'name', None):
            raise TypeError('Name must be specified or set in a subclass')

    def contains(self, name):
        '''
        Returns `bool` representing whether or not zone should contain name
        '''
        # we trim the trailing dot that is returned from the API
        # so we just trim the dot on the fqdn we are passed if it has one
        if name.endswith('.'):
            name = name[:-1]
        return name.endswith(self.name)

    async def update_records(self, *args, ttl=300):
        '''
        Updates in a DNS Zone record(s)
        Arguments::
            *args : must be sequences representing records
            record (sequence) : (Name, type, Value) must be specified
                Value may be list or str

        Typical usage::
            zone.update_records(
                    ('foo.zone.org', 'A', '1.2.3.4''),
                    ('bar.zone.org', 'NS', ['ns1.zone.org', 'ns2.zone.org'])
            )

        Whenever records of a given type are updated, all records not included in the new RR set are deleted.  That is, if the ``A`` records are being updated, all current addresses must be included.
        '''

        raise NotImplementedError


__all__ += ['DnsZone']


class PublicDnsManagement(AsyncInjectable):

    '''
Update a DNS zone when :class:`NetworkLinks` gain a public IP address.  This can be attached to an existing injector if *attach_to* is included on construction.  More typically, this can be used as a Carthage modeling mixin.
    Typical usage in that mode::

        class some_enclave(Enclave, PublicDnsManagement):

            domain = "machines.example.com"
            add_provider(InjectionKey(DnsZone, name='machines.example.com', addressing='public'), some_dns_zone)

            class some_machine(MachineModel): ...

    Then, when `some_machine` gains an IP address, an `A` record will be created.

    '''

    async def public_ip_updated(self, target, **kwargs):
        link = target
        model = link.machine
        name = link.public_dns_name
        if name is None: name = model.name
        if not name: return     # public_dns_name = ''
        stem, sep, domain = name.partition('.')
        if not domain: return
        zone = await self.ainjector.get_instance_async(InjectionKey(
            DnsZone, name=domain, addressing='public', _ready=True, _optional=True))
        if zone is None:
            zone = await self.ainjector.get_instance_async(InjectionKey(DnsZone, role='public_zone', _ready=True, _optional=True))
        if zone is None and link.public_dns_name:
            logger.error(f'No public zone found for {name}')
        elif zone is None:
            logger.debug(f'No public zone found for {name}, but it may not be required')
            
        if not zone.contains(name):
            logger.error(f'Not setting DNS for {model}: {name} does not fall within {zone.name}')
        else:
            logger.debug(f'{name} is at {str(link.merged_v4_config.public_address)}')
            await zone.update_records((name, 'A', [str(link.merged_v4_config.public_address)]),
                                      ttl=30)

    def __init__(self, attach_to=None, **kwargs):
        super().__init__(**kwargs)
        if attach_to is None:
            attach_to = self.injector
        attach_to.add_event_listener(InjectionKey(NetworkLink), 'public_address', self.public_ip_updated)


__all__ += ['PublicDnsManagement']

@inject(ainjector=AsyncInjector)
async def update_dns_for(name, *,
                         public_records,
                         private_records,
                         ainjector, ttl=300,
                         ):
    '''
    Look up the zone for *name*, and update records within it.
    For public records,  ``InjectionKey(DnsZone, name=domain, addressing='public')`` is used.

    If a public zone is found, then ``InjectionKey(DnsZone, name=domain, addressing='private')`` is used for private records.  If a public zone is not found, then ``InjectionKey(DnsZone, name=domain)`` will also be accepted for private records.

    The zone keys used are dependent on what zones are registered in the injector hierarchy, **not** on what parameters are passed in.  That is, if a public zone exists, and only private records are passed in, private dns will only be updated if an explicit ``addressing='private'`` zone is provided by the injector.
    

    :param records: A sequence of (rrtype, values)

    Example Usage::

        await ainjector(update_dns_for,
            "www.foo.com", public_records=[('CNAME', 'foo.com')])

    '''
    head, sep, domain = name.partition('.')
    public_zone = await ainjector.get_instance_async(InjectionKey(DnsZone, name=domain, addressing='public', _optional=True))
    private_zone = await ainjector.get_instance_async(InjectionKey(DnsZone, name=domain, addressing='private', _optional=True))
    if (not public_zone) and (not private_zone):
        private_zone = await ainjector.get_instance_async(InjectionKey(DnsZone, name=domain, _optional=True))
    futures = []
    if public_records and public_zone:
        args = []
        for type, value in public_records:
            args.append((name, type, value))
        futures.append(asyncio.ensure_future(public_zone.update_records(*args, ttl=ttl)))
    elif public_records:
        logger.warning(f'No public zone for {name}')
    if private_zone and private_records:
        args = []
        for type, value in private_records:
            args.append((name, type, value))
        futures.append(asyncio.ensure_future(private_zone.update_records(*args, ttl=ttl)))

    if private_zone is None and public_zone is None:
        logger.warning(f'No DNS zone for {name}')
    if futures:
        await asyncio.gather(*futures)
        

__all__ += ['update_dns_for']
