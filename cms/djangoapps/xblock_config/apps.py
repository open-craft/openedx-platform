"""
xblock_config Application Configuration
"""


from django.apps import AppConfig

import cms.lib.xblock.runtime
import xmodule.x_module  # lint-amnesty, pylint: disable=wrong-import-order


class XBlockConfig(AppConfig):
    """
    Default configuration for the "xblock_config" Django application.
    """
    name = 'cms.djangoapps.xblock_config'
    verbose_name = 'XBlock Configuration'

    def ready(self):
        from openedx.core.lib.xblock_utils import xblock_local_resource_url
        from django.contrib.auth import get_user_model

        # In order to allow blocks to use a handler url, we need to
        # monkey-patch the x_module library.
        # TODO: Remove this code when Runtimes are no longer created by modulestores
        # https://openedx.atlassian.net/wiki/display/PLAT/Convert+from+Storage-centric+runtimes+to+Application-centric+runtimes
        xmodule.x_module.block_global_handler_url = cms.lib.xblock.runtime.handler_url
        xmodule.x_module.block_global_local_resource_url = xblock_local_resource_url

        User = get_user_model()

        username = "sandbox_admin"
        email = "admin@example.com"
        password = "super_user_admin"

        # Para evitar crearlo varias veces
        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username=username, email=email, password=password)