"""FastAPI routers. All application logic lives in the jobscanner package."""

from . import applications_api, config_api, jobs_api, profile_api

ROUTERS = [jobs_api.router, applications_api.router, profile_api.router, config_api.router]

__all__ = ['ROUTERS']
