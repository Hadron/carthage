import logging
from sqlalchemy.orm import Session
from hadron.inventory.admin import models
from ..dependency_injection import inject, Injector, InjectionKey
from .database import *
from ..utils import when_needed
from ..vm import VM
import carthage.hadron_layout
from carthage import base_injector

logger = logging.getLogger('carthage')

@inject(
    injector = Injector)
def provide_networks(injector, session):
    for n in session.query(models.Network):
        if len(n.locations) == 0: continue
        try:
            site_injector = injector(Injector)
            hn = when_needed(HadronNetwork, n, injector = site_injector)
            site_injector.add_provider(site_network_key, hn)
            base_injector.add_provider(InjectionKey(HadronNetwork,
                                                netid = n.netid), hn)
            for s in session.query(models.Slot).join(models.Role).filter(
                    models.Slot.location_id == n.locations[0].id,
                    models.Role.name == "router"):
                r = provide_slot(s, injector = site_injector, session = session)
                try:
                    site_injector.add_provider(site_router_key, r)
                except Exception: pass
        except Exception:
            logger.exception("Error adding network {}".format(n))
                                     
def provide_slot(s, *, session, injector):
    injector = injector(Injector)
    injector.add_provider(InjectionKey('this_slot'), s)
    container =  when_needed(VM,
                       name = s.fqdn(),
                             network_config = carthage.hadron_layout.router_network_config,
                             injector = injector)
    base_injector.add_provider(InjectionKey(Container, host = s.fqdn()), container)
    return container
