"""
MFE API funtions.
"""

from django.conf import settings

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers


# The public API is only the following symbols:
__all__ = [
    # API methods
    "get_mfe_config",
]

def get_mfe_config(mfe=None):
    """
    Return the merged MFE configuration for the given MFE app.
    """
    # Get values from django settings (level 6) or site configuration (level 5)
    legacy_config = _get_legacy_config()

    # Get values from mfe configuration, either from django settings (level 4) or site configuration (level 3)
    mfe_config = configuration_helpers.get_value("MFE_CONFIG", settings.MFE_CONFIG)

    # Get values from mfe overrides, either from django settings (level 2) or site configuration (level 1)
    mfe_config_overrides = {}

    if mfe:
        app_config = configuration_helpers.get_value(
            "MFE_CONFIG_OVERRIDES",
            settings.MFE_CONFIG_OVERRIDES,
        )
        mfe_config_overrides = app_config.get(mfe, {})

    # Merge the three configs in the order of precedence
    return legacy_config | mfe_config | mfe_config_overrides



def _get_legacy_config() -> dict:
    """
    Return legacy configuration values available in either site configuration or django settings.
    """
    return {
        "ENABLE_COURSE_SORTING_BY_START_DATE": configuration_helpers.get_value(
            "ENABLE_COURSE_SORTING_BY_START_DATE",
            settings.FEATURES.get("ENABLE_COURSE_SORTING_BY_START_DATE")
        ),
        "HOMEPAGE_PROMO_VIDEO_YOUTUBE_ID": configuration_helpers.get_value(
            "homepage_promo_video_youtube_id",
            None
        ),
        "HOMEPAGE_COURSE_MAX": configuration_helpers.get_value(
            "HOMEPAGE_COURSE_MAX",
            getattr(settings, 'HOMEPAGE_COURSE_MAX', None),
        ),
        "COURSE_ABOUT_TWITTER_ACCOUNT": configuration_helpers.get_value(
            "course_about_twitter_account",
            getattr(settings, 'PLATFORM_TWITTER_ACCOUNT', None),
        ),
        "NON_BROWSABLE_COURSES": not settings.FEATURES.get("COURSES_ARE_BROWSABLE"),
        "ENABLE_COURSE_DISCOVERY": settings.FEATURES.get("ENABLE_COURSE_DISCOVERY"),
    }
