from __future__ import with_statement
from beaker.container import NamespaceManager, Container
from beaker.exceptions import InvalidCacheBackendError, MissingCacheParameter
from beaker.synchronization import file_synchronizer, null_synchronizer
from beaker.util import verify_directory, SyncDict
import warnings

_client_libs = {}
def _load_client(name='auto'):
    if name in _client_libs:
        return _client_libs[name]

    def _pylibmc():
        global pylibmc
        import pylibmc
        return pylibmc

    def _cmemcache():
        global cmemcache
        import cmemcache
        warnings.warn("cmemcache is known to have serious "
                    "concurrency issues; consider using 'memcache' "
                    "or 'pylibmc'")
        return cmemcache

    def _memcache():
        global memcache
        import memcache
        return memcache

    def _auto():
        for _client in (_pylibmc, _cmemcache, _memcache):
            try:
                return _client()
            except ImportError:
                pass
        else:
            raise InvalidCacheBackendError(
                    "Memcached cache backend requires one "
                    "of: 'pylibmc' or 'memcache' to be installed.")

    clients = {
        'pylibmc':_pylibmc,
        'cmemcache':_cmemcache,
        'memcache':_memcache,
        'auto':_auto
    }
    _client_libs[name] = clib = clients[name]()
    return clib

class MemcachedNamespaceManager(NamespaceManager):
    """Provides the :class:`.NamespaceManager` API over a memcache client library."""

    clients = SyncDict()

    def __new__(cls, *args, **kw):
        memcache_module = kw.pop('memcache_module', 'auto')

        memcache_client = _load_client(memcache_module)

        if memcache_module == 'pylibmc' or \
            memcache_client.__name__.startswith('pylibmc'):
            return object.__new__(PyLibMCNamespaceManager)
        else:
            return object.__new__(MemcachedNamespaceManager)

    def __init__(self, namespace, url,
                        memcache_module='auto',
                        data_dir=None, lock_dir=None,
                        **kw):
        NamespaceManager.__init__(self, namespace)

        _memcache_module = _client_libs[memcache_module]

        if not url:
            raise MissingCacheParameter("url is required")

        if lock_dir:
            self.lock_dir = lock_dir
        elif data_dir:
            self.lock_dir = data_dir + "/container_mcd_lock"
        if self.lock_dir:
            verify_directory(self.lock_dir)

        self.mc = MemcachedNamespaceManager.clients.get(
                        (memcache_module, url),
                        _memcache_module.Client,
                        url.split(';'))

    def get_creation_lock(self, key):
        return file_synchronizer(
            identifier="memcachedcontainer/funclock/%s" %
                    self.namespace,lock_dir = self.lock_dir)

    def _format_key(self, key):
        return self.namespace + '_' + key.replace(' ', '\302\267')

    def __getitem__(self, key):
        return self.mc.get(self._format_key(key))

    def __contains__(self, key):
        value = self.mc.get(self._format_key(key))
        return value is not None

    def has_key(self, key):
        return key in self

    def set_value(self, key, value, expiretime=None):
        if expiretime:
            self.mc.set(self._format_key(key), value, time=expiretime)
        else:
            self.mc.set(self._format_key(key), value)

    def __setitem__(self, key, value):
        self.set_value(key, value)

    def __delitem__(self, key):
        self.mc.delete(self._format_key(key))

    def do_remove(self):
        self.mc.flush_all()

    def keys(self):
        raise NotImplementedError(
                "Memcache caching does not "
                "support iteration of all cache keys")

class PyLibMCNamespaceManager(MemcachedNamespaceManager):
    """Provide thread-local support for pylibmc."""

    def __init__(self, *arg, **kw):
        super(PyLibMCNamespaceManager, self).__init__(*arg, **kw)
        self.pool = pylibmc.ThreadMappedPool(self.mc)

    def __getitem__(self, key):
        with self.pool.reserve() as mc:
            return mc.get(self._format_key(key))

    def __contains__(self, key):
        with self.pool.reserve() as mc:
            value = mc.get(self._format_key(key))
            return value is not None

    def has_key(self, key):
        return key in self

    def set_value(self, key, value, expiretime=None):
        with self.pool.reserve() as mc:
            if expiretime:
                mc.set(self._format_key(key), value, time=expiretime)
            else:
                mc.set(self._format_key(key), value)

    def __setitem__(self, key, value):
        self.set_value(key, value)

    def __delitem__(self, key):
        with self.pool.reserve() as mc:
            mc.delete(self._format_key(key))

    def do_remove(self):
        with self.pool.reserve() as mc:
            mc.flush_all()

class MemcachedContainer(Container):
    """Container class which invokes :class:`.MemcacheNamespaceManager`."""
    namespace_class = MemcachedNamespaceManager
