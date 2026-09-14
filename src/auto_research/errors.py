"""Errors which callers may report without an internal traceback."""


class ResearchError(Exception):
    """A rejected operation or unavailable research record."""


class ValidationError(ResearchError, ValueError):
    pass


class NotFoundError(ResearchError, LookupError):
    pass


class ConflictError(ResearchError):
    pass


class BudgetError(ConflictError):
    pass
