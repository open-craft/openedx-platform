"""
Middleware for the login / registration views.
"""

import logging
from http.cookies import CookieError, Morsel

from django.utils.http import http_date

log = logging.getLogger(__name__)

# Requests to these paths clear the cookies of the parent domain.
COOKIE_RESET_PATHS = ('/login', '/login/')

# Cookies are never scoped to a bare public suffix (e.g. `com`), so a parent
# domain shorter than this is left alone.
MIN_DOMAIN_LABELS = 2


class ClearCookiesOnLoginPageMiddleware:
    """
    Expire the cookies of the domain above the login page, keeping the ones
    scoped to its own host.

    Stale cookies left over on the parent domain (typically `sessionid`,
    `csrftoken` or the JWT cookies) are sent alongside the ones of the login
    page and shadow them, which breaks the login flow. The browser tells us the
    name and the value of a cookie, but never the domain it was scoped to, so
    each name is expired once on the parent domain. A login page served from
    `my.lms.example.com` therefore clears `lms.example.com`, while keeping both
    the cookies of `my.lms.example.com` and the host-only ones.

    The cookies that the login page itself sets are kept, even on the parent
    domain, which is where they land when `SESSION_COOKIE_DOMAIN` points there.

    This must be listed near the top of `MIDDLEWARE` so that its response phase
    runs after `SafeSessionMiddleware` and `CsrfViewMiddleware` have set their
    cookies.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.path in COOKIE_RESET_PATHS and request.COOKIES:
            _expire_parent_domain_cookies(request, response)

        return response


def _parent_domain(request):
    """
    Return the domain one level above the host of `request`.

    `None` is returned for a host with nothing to clear above it, such as
    `example.com`, whose parent is the `com` public suffix.
    """
    host = request.get_host().partition(':')[0].strip('.')
    _, _, parent = host.partition('.')

    if not parent or parent.count('.') + 1 < MIN_DOMAIN_LABELS:
        return None

    return parent


def _expire_parent_domain_cookies(request, response):
    """
    Add a `Set-Cookie` header expiring each cookie of the request on the parent
    domain, except for the ones that the response already sets there.
    """
    domain = _parent_domain(request)
    if not domain:
        return

    # The dot of a `.example.com` domain is stripped before the comparison, as
    # the browser stores such a cookie under `example.com`. Skipping that would
    # expire the cookies that the login page has just set.
    cookies_set_by_response = {
        (morsel.key, morsel['domain'].strip('.'), morsel['path'] or '/')
        for morsel in response.cookies.values()
    }

    for name in request.COOKIES:
        if (name, domain, '/') not in cookies_set_by_response:
            _expire_cookie(response, name, domain, secure=request.is_secure())


def _expire_cookie(response, name, domain, secure):
    """
    Expire the `name` cookie of `domain` on the root path.

    `response.cookies` is keyed by cookie name, so `response.delete_cookie`
    would expire the name of a cookie that the login page sets itself. The
    morsel is inserted under a key of its own instead, bypassing that
    limitation; only `Morsel.key` is written to the header.
    """
    morsel = Morsel()
    try:
        morsel.set(name, '', '')
    except CookieError:
        log.warning('Not expiring the cookie with an unsupported name: %r.', name)
        return

    morsel['path'] = '/'
    morsel['domain'] = domain
    morsel['expires'] = http_date(0)
    morsel['max-age'] = 0
    if secure:
        morsel['secure'] = True

    dict.__setitem__(response.cookies, f'{name}@{domain}', morsel)
