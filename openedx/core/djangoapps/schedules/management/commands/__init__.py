"""
Base management command for sending emails
"""


import datetime

import pytz
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db.models import Q

from openedx.core.djangoapps.schedules.utils import PrefixedDebugLoggerMixin

ALL_SITES = "all-sites"


class SendEmailBaseCommand(PrefixedDebugLoggerMixin, BaseCommand):  # lint-amnesty, pylint: disable=missing-class-docstring
    async_send_task = None  # define in subclass

    # An iterable of day offsets (e.g. -7, -14, -21, -28, ...) that defines the days for
    # which emails are sent out, relative to the 'date' parameter
    offsets = range(-7, -77, -7)

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            default=datetime.datetime.utcnow().date().isoformat(),
            help='The date to compute weekly messages relative to, in YYYY-MM-DD format',
        )
        parser.add_argument(
            '--override-recipient-email',
            help='Send all emails to this address instead of the actual recipient'
        )
        parser.add_argument(
            'site_domain_name',
            nargs='?',
            default=ALL_SITES
        )
        parser.add_argument(
            '--weeks',
            type=int,
            help='Number of weekly emails to be sent',
        )
        parser.add_argument(
            '--override-middlewares',
            action='append',
            help='Use these middlewares when emulating http requests.'
        )

    def handle(self, *args, **options):
        self.log_debug('Args = %r', options)

        num_weeks = options.get('weeks')
        if num_weeks:
            num_days = (7 * num_weeks) + 1
            self.offsets = range(-7, -num_days, -7)

        current_date = datetime.datetime(
            *[int(x) for x in options['date'].split('-')],
            tzinfo=pytz.UTC
        )
        self.log_debug('Current date = %s', current_date.isoformat())
        override_recipient_email = options.get('override_recipient_email')
        override_middlewares = options.get('override_middlewares')

        site_domain_name = options['site_domain_name']
        site_filter = Q()
        if site_domain_name != ALL_SITES:
            site_filter |= Q(domain__iexact=site_domain_name)

        for site in Site.objects.filter(site_filter):
            self.log_debug('Running for site %s', site.domain)
            self.send_emails(site, current_date, override_recipient_email, override_middlewares)

    def enqueue(self, day_offset, site, current_date, override_recipient_email=None, override_middlewares=None):
        self.async_send_task.enqueue(
            site,
            current_date,
            day_offset,
            override_recipient_email,
            override_middlewares,
        )

    def send_emails(self, *args, **kwargs):
        for offset in self.offsets:
            self.enqueue(offset, *args, **kwargs)
