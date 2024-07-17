# Copyright (C) 2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import collections.abc

import yaml
from pathlib import Path
from ipaddress import IPv4Address

import lmdb

from .dependency_injection import *
from .config import ConfigLayout
from .utils import memoproperty


__all__ = []

#: An injection key whose value is a path to be loaded into
#:class:`KvStore` at initialization time.  Typical usage is to
#periodically dump the dumpable domains (such as hints about IP
#address assignment) from the *KvStore* into a file checked into the
#source of a :class:`carthage.modeling.CarthageLayout`.  That layout
#then provides persistent_seed_path in the main injector of the
#layout.  At startup, the layout will load the hints file, so that
#multiple runs of the layout with different state directories will
#have consistent assignments.  Note that this must either be provided
#on the base injector or directly on a *CarthageLayout*.  There is
#special logic in *CarthageLayout* to perform the load when this key
#is set there.
persistent_seed_path = InjectionKey('carthage.kvstore/persistent_seed_path')

__all__ += ['persistent_seed_path']

@inject_autokwargs(config_layout=ConfigLayout,
                   persistent_seed_path=InjectionKey(persistent_seed_path, _optional=True),
                   )
class KvStore(Injectable):

    def __init__(
            self, store_dir="persistent_assignments", max_size=4*2**30,
            **kwargs):
        super().__init__(**kwargs)
        store_path = Path(store_dir)
        if not store_path.is_absolute():
            store_path = Path(self.config_layout.state_dir)/store_path
            Path(self.config_layout.state_dir).mkdir(parents=True, exist_ok=True)
        self.environment = lmdb.Environment(
            str(store_path), subdir=True,
            max_dbs=512,
            map_size=max_size,
            create=True,
            writemap=True)
        if self.persistent_seed_path:
            persistent_seed_path = Path(self.persistent_seed_path)
            if persistent_seed_path.exists():
                self.load(persistent_seed_path)
                

    def close(self):
        self.environment.close()

    def domain(self, d:str, include_in_dump):
        '''Return a :class:`KvDomain` for accessing a domain of keys in the Store.  Typical usage::

            kvstore = KvStore(path)
            domain = kvstore.domain('network/foo_net/addresses', True)
            domain.put('30', 'foo.com')   # foo.com is address 30 on this network

        :param include_in_dump: If True, then the contents of this domain should be included in the results of a call to :meth:`dump`
        '''
        if include_in_dump:
            with self.environment.begin(write=True) as txn:
                assert txn.put(b'dump:'+bytes(d, 'utf-8'), b'true', True)
        return KvDomain(self, d)

    def dump(self, file, filter):
        domains = set()
        file = Path(file)
        result = dict()
        with self.environment.begin() as txn, txn.cursor() as csr:
            csr.set_range(b'dump')
            for key, value in csr:
                if not key.startswith(b'dump:'): break
                domains.add(key[5:])
            for d in domains:
                domain_key = d+b':'
                csr.set_range(domain_key)
                domain_result = dict()
                for key, value in csr:
                    if not key.startswith(domain_key): continue
                    key = key[len(domain_key):]
                    key_str = str(key, 'utf-8')
                    value_str = str(value, 'utf-8')
                    if filter(str(d, 'utf-8'), key_str, value_str):
                        domain_result[key_str] = value_str
                result[str(d, 'utf-8')] = domain_result
        file.write_text(yaml.dump(result, default_flow_style=False))

    def load(self, file):
        '''Load a dump file produced by :meth:`dump`.  Values aready in the :class:`KvStore` override values in the dump.

        The intent is that a dump file can be checked into a layout repository as an initial set of assignments for things like IP addresses and MAC addresses.  An actual running state_dir for the layout may diverge from the initial hints contained in the dump.  Periodically the layout repository can be updated.
        '''
        file = Path(file)
        result = yaml.safe_load(file.read_text())
        with self.environment.begin(write=True) as txn:
            for domain, domain_dict in result.items():
                for k,v in domain_dict.items():
                    txn.put(kv_key(domain, k), bytes(v, 'utf-8'))
                    # Does not overwrite existing values
                    
    

__all__ += ['KvStore']

def kv_key(domain, key):
    domain = domain.replace(':', '::')
    return bytes(domain+':'+key, 'utf-8')


class KvDomain:

    def __init__(self, store, domain):
        self.domain = domain
        self.environment = store.environment

    def put(self, k, v, *,
            value=None,
            overwrite=False):
        '''Setself[*k* to *v*.  If *overwrite* is False, raise :class:`KvConsistency` if *k* in self and not self[*k*] == *value*.
        '''
        assert not ( value and overwrite), "Specifying overwrite and value nonsensical"
        key = kv_key(self.domain, k)
        v_bytes = bytes(v, 'utf-8')
        if value:
            value_bytes = bytes(value, 'utf-8')
        else: value_bytes = None
        with self.environment.begin(write=True) as txn:
            if value_bytes:
                actual_value = txn.get(key)
                if actual_value != value_bytes:
                    raise KvConsistency(f'Expecting {k} == {value} but actually {value_bytes}')
            if not txn.put(key, v_bytes, overwrite=(overwrite or value is not None)):
                raise KvConsistency(f'{k} present in {self.domain}')
            

    def get(self, k, default=None):
        '''Returns self[*k*] or if not present *default*'''
        key = kv_key(self.domain, k)
        with self.environment.begin() as txn:
            v = txn.get(key, NotPresent)
            if v == NotPresent: return default
            return str(v, 'utf-8')

    def delete(self, k, value=NotPresent):
        '''Removes *k* from self or raises :class:`KvConsistency`
        If *value* is given, then self[*k*] must equal *value* before the delete.
'''
        key = kv_key(self.domain, k)
        with self.environment.begin(write=True) as txn, \
             txn.cursor() as csr:
            csr.set_key(key)
            if csr.key() != key:
                raise KvConsistency(f'{k} not in {self.domain}')
            if value is not NotPresent:
                value_bytes = bytes(value, 'utf-8')
                if csr.value() != value_bytes:
                    raise KvConsistency(f'{k} in {self.domain} had unexpected value')
            csr.delete()


    def __getitem__(self, k):
        v = self.get(k, NotPresent)
        if v is NotPresent:
            raise KeyError(k)
        return v

    def __delitem__(self, k):
        try: self.delete(k)
        except KvConsistency:
            raise KeyError(k) from None
        

class KvConsistency(RuntimeError):
#    def __init__(self, *args):
#        breakpoint()

    pass


__all__ += ['KvConsistency']


class AssignmentsExhausted(RuntimeError): pass

__all__ += ['AssignmentsExhausted']


@inject_autokwargs(
    store=KvStore,
    )
class HintedAssignments(Injectable):

    #: How many times to retry an assignment when we lose a race against another process.
    consistency_retries = 5

    def __init__(self, domain, **kwargs):
        super().__init__(**kwargs)
        self._assignments = self.store.domain(domain+'/assignments', False)
        self._hints = self.store.domain(domain+'/hints', True)
        self._can_validate_assignments = False
        self.prefer_reallocate = False #: move things around when the preferred assignment changes
        self.new_assignments()


    def new_assignments(self):
        "Indicate a new round of assignments is beginning.  The same assignment will never be used more than once in a single round of assignments, but may be reused across rounds."
        self._assignments_made = dict()

    def _assign(self,  key, obj):
        '''
Called in subclasses to indicate that an assignment should be made.  :meth:`record_assignment` is called to let the object know what its assignment is.

        :param key: The key under which an assignment or hint is registered.  Should be unique across runs for the same object.

        :param obj: The object corresponding to *key*.  Not used by :class:`HintedAssignments` except as an input to the subclass's :meth:`record_assignment`
        '''
        for i in range(self.consistency_retries):
            try:
                hint = self._hints.get(key)
                if hint and self.valid_assignment(hint, obj):
                    # We always try to reuse a hint
                    if self._try_assignment(key, obj, hint, True):
                        return
                    else:
                        try: self._hints.delete(key, value=hint)
                        except KvConsistency:
                            logger.debug(f'Tried deleting hint for {key} but it was not {hint}')
                # No hint
                reusable_assignment = None
                for assignment in self.possible_assignments(key, obj):
                    result = self._try_assignment(key, obj, assignment, self.prefer_reallocate)
                    if result is True: return
                    if result == "reusable":
                        # If we preferred reusing an assignment, then we already would have done so.
                        # Remember the first reusable assignment and use if all assignments are exhausted.
                        if reusable_assignment is None: reusable_assignment = assignment
                if reusable_assignment:
                    if self._try_assignment(key, obj, reusable_assignment, True):
                        return
                raise AssignmentsExhausted(f'Assignments for {self} exhausted')
                
            except KvConsistency:
                continue
        raise KvConsistency(f'Exceeded maximum retries')


    def _try_assignment(self, key, obj, assignment, reallocate_assigned):
        '''
        Called to  try and assign *assignment* as the assignment for *key*.  This can work either if:

        * *assignment* is unassigned in the store of assignments

        *Or *assignment* is assigned to a key for which :meth:`valid_key` returns False.  In this case the assignment is reusable.

          If an assignment is reusable and *reallocate_assign* is True, then the assignment is reallocated.  If *reallocate_assign* is False, then ``'reuseable'`` is returned.

        :returns: True if the assignment is made; False if the requested assignment is allocated to a valid key; or ``'reusable'`` if the assignment could be reallocated but *reallocate_assignment* is False.

        '''
        try: del self._assignments_made[key]
        except KeyError: pass
        current_key = self._assignments.get(str(assignment))
        if current_key in self._assignments_made and self._assignments_made[current_key] == str(assignment):
            return False   # Has been allocated in this round to someone else
        elif current_key in self._assignments_made:
            # Has been assigned a different assignment this round
            try: self._assignments.delete(key, value=current_key)
            except KvConsistency: return False
            # If we did delete the assignment, then we can use the
            # key.  Don't count as a reallocate, because the previous
            # allocated key has already moved.
            current_key = None
        if current_key and (current_key != key):   # Potentially allocated see if still valid
            if not self._can_validate_assignments: return False   # Models not loaded enough to know what all the valid keys are
            if self.valid_key(current_key):
                return False   # Allocated to valid key
            # It's reusable
            if not reallocate_assigned: return 'reusable'
        # Is available for us to assign to key
        self._assignments.put(str(assignment), key, value=current_key)
        # Past this point we should not get KvConsistency errors.
        self._hints.put(key, str(assignment), overwrite=True)
        # There is a race.  By recording the assignment before the hint, if
        # someone else is also assigning the same object at the same time,
        # they may not get the hint and may try enumerating possible
        # assignments.  Assuming possible_assignments is stable, they will end
        # up with the same result.  If not, it is possible there could be
        # churn.  By recording the hint as soon as we can, we minimize this.
        # We could avoid this with some sort of multiput if it allowed us to
        # require a value for one key but not the other.  Redis doesn't appear
        # to have that.  Based on expected usage, we accept the race.
        self.record_assignment(key, obj, assignment)
        self._assignments_made[key] = str(assignment)
        return True

    def force_assignment(self, key, obj, assignment):
        '''Force recording within the data store that *obj* identified by *key* has *assignment* as its assignment.  This is intended for dealing with statically assigned assignments that fall into the range that is automatically managed.  Does not call :meth:`record_assignment`
        '''
        self._assignments.put(key, str(assignment), overwrite=True)
        self._hints.put(key, str(assignment), overwrite=True)
        self._assignments_made[key] = str(assignment)
        
    def record_assignment(self, key, obj, assignment):
        '''
        Implemented by subclasses to inform an object of its assignment.
        As an example if being used for IP address asignment, the subclass version of this method would be responsible for setting the address on the :class:`carthage.network.NetworkLink`.
        Note that *assignment* may be a string, even if :meth:`possible_assignments` yields something like integers.  This will happen for example when a potential assignment is retrieved from  a hint.
        '''
        raise NotImplementedError


    def possible_assignments(self, key, obj):
        '''Implemented by subclasses.  This generator yields all the possible assignments for a given *obj* in preference order.
It is best if this generator yields strings; ass discussed in the documentation for :meth:`record_assignment` at least for hints the assignment code will coerce assignments to strings.
'''
        raise NotImplementedError

    
    def enable_key_validation(self):
        '''Called by the subclass when valid_key can be called.  Prior to this method being called, valid_key will never be called under the assumption that not all valid keys are known.  So prior to this method being called, no assignments can be reallocated.
        '''
        self._can_validate_assignments = True

    def valid_assignment(self, assignment, obj):
        '''
        Implemented by subclass. Return True if *assignment* is still a valid assignment for this collection.  Used to confirm a hint is reasonable and to cleanup assignments that are no longer possible.
        If the possibility of an illegal assignment still being hinted is not an issue even when the set returned by possible_assignments changes, this method can always return True.
        '''
        return True

    def valid_key(self, key):
        '''
        Implemented by subclass. Returns True if *key* represent an object that we still want to track assignments for. If this returns False then resources assigned to *key* may be reallocated for other keys.
        '''
        raise NotImplementedError


__all__ += ['HintedAssignments']


class HashedRangeAssignments(HintedAssignments):

    def __init__(self, domain,  **kwargs):
        super().__init__(domain, **kwargs)

    

    def hash_key(self, key, obj):
        '''Key hashed, bounded to low <= key <= high
:returns: low, hash, high
'''
        assert isinstance(key, str)
        low, high = self.find_bounds(obj)
        result = 0
        for c in key: result += ord(c)
        try: size = high-low +1
        except TypeError:
            size = int(high)-int(low)+1
        return low, low + (result % size), high

    

    def possible_assignments(self, key, obj):
        '''Finds the bounds for *obj* using :meth:`find_bounds` then returns all assignments within the bounds:

        * Initially ``self.hash_key())

        * If that doesn't work, try at a distance of 1, rejecting either ``hash+1`` or ``hash-1`` if it falls outside the bounds
       
        * Increase distance, stopping iteration if both ``hash+distance`` and ``hash-distance`` are out of bounds.

        '''
        low, high = self.find_bounds(obj)
        low, hash, high = self.hash_key(key, obj)
        result_yielded = True
        distance = 0
        while result_yielded:
            result_yielded = False
            if low <= (hash+distance) <= high:
                yield str(hash+distance)
                result_yielded = True
            if (distance > 0) and (low <= hash-distance <= high):
                yield str(hash-distance)
                result_yielded = True
            distance += 1

    def find_bounds(self, obj):
        '''Implemented by subclass; returns tuple of low, high, for the range of assignments for this object.
        '''
        raise NotImplementedError

    def valid_assignment(self, assignment, obj):
        assignment = self.str_to_assignment(assignment)
        low, high = self.find_bounds(obj)
        if low <= assignment <= high: return True
        return False

    def str_to_assignment(self, s):
        '''Override if needed to convert a string into an assignment useful for valid_assignment
'''
        return int(s)

__all__ += ['HashedRangeAssignments']

