"""Customer Portal BFF infrastructure."""

from .app import (
    CustomerPortalOidcAuthorizationStart,
    CustomerPortalOidcBroker,
    CustomerPortalOidcRejected,
    UnconfiguredCustomerPortalOidcBroker,
    create_customer_portal_app,
)

__all__ = [
    "CustomerPortalOidcAuthorizationStart",
    "CustomerPortalOidcBroker",
    "CustomerPortalOidcRejected",
    "UnconfiguredCustomerPortalOidcBroker",
    "create_customer_portal_app",
]

from .composition import build_customer_portal_app as build_customer_portal_app

__all__.append("build_customer_portal_app")
