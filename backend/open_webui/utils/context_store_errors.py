"""Safe, HTTP-mappable storage errors without database/application imports."""


class ContextStoreError(Exception):
    status_code = 503
    code = 'temporary_context_unavailable'
    retryable = True
    rebuild_allowed = False

    def __init__(self, *, limit=None, rebuild_allowed=False):
        self.limit = limit
        self.rebuild_allowed = rebuild_allowed
        super().__init__(self.code)

    def detail(self):
        value = {'code': self.code, 'retryable': self.retryable, 'rebuild_allowed': self.rebuild_allowed}
        if self.limit is not None:
            value['limit'] = self.limit
        return value


class ContextNotFound(ContextStoreError):
    status_code = 404
    code = 'temporary_context_not_found'
    retryable = False


class ContextGone(ContextStoreError):
    status_code = 410
    code = 'temporary_context_gone'
    retryable = False


class ContextConflict(ContextStoreError):
    status_code = 409
    code = 'temporary_context_conflict'


class ContextUnavailable(ContextStoreError):
    pass


class ContextCapacity(ContextStoreError):
    status_code = 413
    code = 'temporary_context_capacity_exceeded'
    retryable = False

    def __init__(self, *, limit='context'):
        super().__init__(limit=limit)
        if limit == 'live_contexts':
            self.status_code = 429
