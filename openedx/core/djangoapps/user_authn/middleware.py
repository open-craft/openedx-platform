"""
Middleware for the login / registration views.
"""

import logging
from http.cookies import CookieError, Morsel

from django.conf import settings
from django.utils.http import http_date

log = logging.getLogger(__name__)

# Requests to these paths leave the browser with a clean cookie jar.
COOKIE_RESET_PATHS = ('/login', '/login/')

# Cookies are never scoped to a bare public suffix (e.g. `.io`), so stop there
# when walking up the parent domains.
MIN_DOMAIN_LABELS = 2


class ClearCookiesOnLoginPageMiddleware:
    """
    Expire every cookie the browser sent to the login page, keeping only the
    ones the login page itself sets.

    Stale cookies left over on a parent domain (typically `sessionid`,
    `csrftoken` or the JWT cookies) are sent alongside the freshly set ones and
    shadow them, which breaks the login flow. The browser tells us the name and
    the value of a cookie, but never the domain it was scoped to, so each name
    is expired once per domain it could have been set on: the request host,
    `SESSION_COOKIE_DOMAIN`, all of their parent domains, each with and without
    the leading dot, plus the host-only variant.

    This must be listed near the top of `MIDDLEWARE` so that its response phase
    runs after `SafeSessionMiddleware` and `CsrfViewMiddleware` have set their
    cookies.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.path in COOKIE_RESET_PATHS and request.COOKIES:
            _expire_stale_cookies(request, response)

        return response


def _domain_variants(domain):
    """
    Yield `domain`, its parent domains, and the leading-dot form of each.
    """
    labels = domain.split('.')
    for index in range(len(labels) - MIN_DOMAIN_LABELS + 1):
        parent = '.'.join(labels[index:])
        yield parent
        yield f'.{parent}'


def _cookie_domains(request):
    """
    Return every domain a cookie sent with `request` could have been scoped to.
    """
    domains = {None}  # Host-only cookies, i.e. those set without a `Domain` attribute.

    host = request.get_host().partition(':')[0].strip('.')
    if host:
        domains.update(_domain_variants(host))

    session_cookie_domain = (settings.SESSION_COOKIE_DOMAIN or '').strip('.')
    if session_cookie_domain:
        domains.update(_domain_variants(session_cookie_domain))

    return domains


def _expire_stale_cookies(request, response):
    """
    Add a `Set-Cookie` header expiring each cookie of the request, except for
    the ones the response already sets.
    """
    cookies_set_by_response = {
        (morsel.key, morsel['domain'] or None, morsel['path'] or '/')
        for morsel in response.cookies.values()
    }

    for name in request.COOKIES:
        for domain in _cookie_domains(request):
            if (name, domain, '/') not in cookies_set_by_response:
                _expire_cookie(response, name, domain, secure=request.is_secure())


def _expire_cookie(response, name, domain, secure):
    """
    Expire the `name` cookie of `domain` on the root path.

    `response.cookies` is keyed by cookie name, so `response.delete_cookie` can
    only expire a name on a single domain. The morsel is inserted under a key of
    its own instead, bypassing that limitation; only `Morsel.key` is written to
    the header.
    """
    morsel = Morsel()
    try:
        morsel.set(name, '', '')
    except CookieError:
        log.warning('Not expiring the cookie with an unsupported name: %r.', name)
        return

    morsel['path'] = '/'
    if domain:
        morsel['domain'] = domain
    morsel['expires'] = http_date(0)
    morsel['max-age'] = 0
    if secure:
        morsel['secure'] = True

    dict.__setitem__(response.cookies, f'{name}@{domain or ""}', morsel)
