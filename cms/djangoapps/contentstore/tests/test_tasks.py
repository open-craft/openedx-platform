"""
Unit tests for course import and export Celery tasks
"""


import copy
import json
from unittest import mock
from uuid import uuid4

import ddt
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.locator import CourseLocator
from organizations.models import OrganizationCourse
from organizations.tests.factories import OrganizationFactory
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from cms.djangoapps.contentstore.tasks import (
    export_olx,
    update_special_exams_and_publish,
    rerun_course,
    sync_discussion_settings,
)
from cms.djangoapps.contentstore.tests.test_libraries import LibraryTestCase
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.course_apps.toggles import EXAMS_IDA
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider
from openedx.core.djangoapps.embargo.models import Country, CountryAccessRule, RestrictedCourse
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE

TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex


def side_effect_exception(*args, **kwargs):
    """
    Side effect for mocking which raises an exception
    """
    raise Exception('Boom!')


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ExportCourseTestCase(CourseTestCase):
    """
    Tests of the export_olx task applied to courses
    """

    def test_success(self):
        """
        Verify that a routine course export task succeeds
        """
        key = str(self.course.location.course_key)
        result = export_olx.delay(self.user.id, key, 'en')
        status = UserTaskStatus.objects.get(task_id=result.id)
        self.assertEqual(status.state, UserTaskStatus.SUCCEEDED)
        artifacts = UserTaskArtifact.objects.filter(status=status)
        self.assertEqual(len(artifacts), 1)
        output = artifacts[0]
        self.assertEqual(output.name, 'Output')

    @mock.patch('cms.djangoapps.contentstore.tasks.export_course_to_xml', side_effect=side_effect_exception)
    def test_exception(self, mock_export):  # pylint: disable=unused-argument
        """
        The export task should fail gracefully if an exception is thrown
        """
        key = str(self.course.location.course_key)
        result = export_olx.delay(self.user.id, key, 'en')
        self._assert_failed(result, json.dumps({'raw_error_msg': 'Boom!'}))

    @mock.patch('cms.djangoapps.contentstore.tasks.User.objects.get', side_effect=User.DoesNotExist)
    def test_invalid_user_id(self, mock_raise_exc):  # pylint: disable=unused-argument
        """
        Verify that attempts to export a course as an invalid user fail
        """
        user = UserFactory(id=User.objects.order_by('-id').first().pk + 100)
        key = str(self.course.location.course_key)
        result = export_olx.delay(user.id, key, 'en')
        self._assert_failed(result, f'Unknown User ID: {user.id}')

    def test_non_course_author(self):
        """
        Verify that users who aren't authors of the course are unable to export it
        """
        _, nonstaff_user = self.create_non_staff_authed_user_client()
        key = str(self.course.location.course_key)
        result = export_olx.delay(nonstaff_user.id, key, 'en')
        self._assert_failed(result, 'Permission denied')

    def _assert_failed(self, task_result, error_message):
        """
        Verify that a task failed with the specified error message
        """
        status = UserTaskStatus.objects.get(task_id=task_result.id)
        self.assertEqual(status.state, UserTaskStatus.FAILED)
        artifacts = UserTaskArtifact.objects.filter(status=status)
        self.assertEqual(len(artifacts), 1)
        error = artifacts[0]
        self.assertEqual(error.name, 'Error')
        self.assertEqual(error.text, error_message)


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ExportLibraryTestCase(LibraryTestCase):
    """
    Tests of the export_olx task applied to libraries
    """

    def test_success(self):
        """
        Verify that a routine library export task succeeds
        """
        key = str(self.lib_key)
        result = export_olx.delay(self.user.id, key, 'en')
        status = UserTaskStatus.objects.get(task_id=result.id)
        self.assertEqual(status.state, UserTaskStatus.SUCCEEDED)
        artifacts = UserTaskArtifact.objects.filter(status=status)
        self.assertEqual(len(artifacts), 1)
        output = artifacts[0]
        self.assertEqual(output.name, 'Output')


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class RerunCourseTaskTestCase(CourseTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def _rerun_course(self, old_course_key, new_course_key):
        CourseRerunState.objects.initiated(old_course_key, new_course_key, self.user, 'Test Re-run')
        rerun_course(str(old_course_key), str(new_course_key), self.user.id)

    def test_success(self):
        """ The task should clone the OrganizationCourse and RestrictedCourse data. """
        old_course_key = self.course.id
        new_course_key = CourseLocator(org=old_course_key.org, course=old_course_key.course, run='rerun')

        old_course_id = str(old_course_key)
        new_course_id = str(new_course_key)

        organization = OrganizationFactory(short_name=old_course_key.org)
        OrganizationCourse.objects.create(course_id=old_course_id, organization=organization)

        restricted_course = RestrictedCourse.objects.create(course_key=self.course.id)
        restricted_country = Country.objects.create(country='US')

        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=restricted_course,
            country=restricted_country
        )

        # Run the task!
        self._rerun_course(old_course_key, new_course_key)

        # Verify the new course run exists
        course = modulestore().get_course(new_course_key)
        self.assertIsNotNone(course)

        # Verify the OrganizationCourse is cloned
        self.assertEqual(OrganizationCourse.objects.count(), 2)
        # This will raise an error if the OrganizationCourse object was not cloned
        OrganizationCourse.objects.get(course_id=new_course_id, organization=organization)

        # Verify the RestrictedCourse and related objects are cloned
        self.assertEqual(RestrictedCourse.objects.count(), 2)
        restricted_course = RestrictedCourse.objects.get(course_key=new_course_key)

        self.assertEqual(CountryAccessRule.objects.count(), 2)
        CountryAccessRule.objects.get(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=restricted_course,
            country=restricted_country
        )


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class RegisterExamsTaskTestCase(CourseTestCase):  # pylint: disable=missing-class-docstring

    @mock.patch('cms.djangoapps.contentstore.exams.register_exams')
    @mock.patch('cms.djangoapps.contentstore.proctoring.register_special_exams')
    def test_exam_service_not_enabled_success(self, _mock_register_exams_proctoring, _mock_register_exams_service):
        """ edx-proctoring interface is called if exam service is not enabled """
        update_special_exams_and_publish(str(self.course.id))
        _mock_register_exams_proctoring.assert_called_once_with(self.course.id)
        _mock_register_exams_service.assert_not_called()

    @mock.patch('cms.djangoapps.contentstore.exams.register_exams')
    @mock.patch('cms.djangoapps.contentstore.proctoring.register_special_exams')
    @override_waffle_flag(EXAMS_IDA, active=True)
    def test_exam_service_enabled_success(self, _mock_register_exams_proctoring, _mock_register_exams_service):
        """ exams service interface is called if exam service is enabled """
        update_special_exams_and_publish(str(self.course.id))
        _mock_register_exams_proctoring.assert_not_called()
        _mock_register_exams_service.assert_called_once_with(self.course.id)

    @mock.patch('cms.djangoapps.contentstore.exams.register_exams')
    @mock.patch('cms.djangoapps.contentstore.proctoring.register_special_exams')
    def test_register_exams_failure(self, _mock_register_exams_proctoring, _mock_register_exams_service):
        """ credit requirements update signal fires even if exam registration fails """
        with mock.patch('openedx.core.djangoapps.credit.signals.handlers.on_course_publish') as course_publish:
            _mock_register_exams_proctoring.side_effect = Exception('boom!')
            update_special_exams_and_publish(str(self.course.id))
            course_publish.assert_called()


@ddt.ddt
@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class SyncDiscussionSettingsTaskTestCase(CourseTestCase):
    """Tests for the `sync_discussion_settings` task."""

    def setUp(self):
        super().setUp()
        self.discussion_config = DiscussionsConfiguration.objects.create(context_key=self.course.id)

    def _update_discussion_settings(self, discussions_settings: dict):
        """Helper method to set discussion settings in the course."""
        self.course.discussions_settings = discussions_settings
        modulestore().update_item(self.course, self.user.id)

    def test_sync_settings(self):
        """Test syncing discussion settings to DiscussionsConfiguration."""
        self._update_discussion_settings(
            {
                "enable_graded_units": True,
                "unit_level_visibility": False,
                "enable_in_context": True,
                "posting_restrictions": "enabled",
            }
        )

        sync_discussion_settings(self.course.id, self.user)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.enable_graded_units is True
        assert self.discussion_config.unit_level_visibility is False
        assert self.discussion_config.enable_in_context is True
        assert self.discussion_config.posting_restrictions == "enabled"
        assert self.discussion_config.provider_type == Provider.LEGACY

    def test_sync_plugin_configuration(self):
        """Test syncing plugin configuration from provider settings."""
        # Set up course discussion settings with provider-specific config
        provider_config = {"test_key": "test_value", "test_key_2": "test_value_2"}
        self._update_discussion_settings({self.discussion_config.provider_type: provider_config})

        sync_discussion_settings(self.course.id, self.user)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.plugin_configuration == provider_config

    @ddt.data(
        (False, True),  # When the tab is visible, the discussion should be enabled.
        (True, False),  # When the tab is hidden, the discussion should be disabled.
    )
    @ddt.unpack
    def test_sync_discussion_tab_visibility(self, is_hidden: bool, expected_enabled: bool):
        """Test syncing discussion enabled status based on tab visibility."""
        for tab in self.course.tabs:
            if tab.tab_id == "discussion":
                tab.is_hidden = is_hidden
                break
        modulestore().update_item(self.course, self.user.id)

        sync_discussion_settings(self.course.id, self.user)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.enabled is expected_enabled

    @override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, active=True)
    def test_auto_migrate_to_new_structure(self):
        """Test automatic migration to the `OPEN_EDX` provider when new structure is enabled."""
        with self.assertLogs("cms.djangoapps.contentstore.tasks", level="INFO") as logs:
            sync_discussion_settings(self.course.id, self.user)

            migration_log = f"New structure is enabled, also updating {self.course.id} to use new provider"
            assert any(migration_log in log for log in logs.output)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.provider_type == Provider.OPEN_EDX

        course = modulestore().get_course(self.course.id)
        assert course.discussions_settings.get("provider_type") == Provider.OPEN_EDX

    @ddt.data(
        {"provider_type": Provider.OPEN_EDX},  # Using the `provider_type` field.
        {"provider": Provider.OPEN_EDX},  # Using the `provider` field as fallback.
    )
    @override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, active=True)
    def test_no_provider_migration_when_already_openedx(self, provider_settings: dict):
        """Test no migration occurs when provider is already `OPEN_EDX`."""
        self._update_discussion_settings(provider_settings)

        with self.assertLogs("cms.djangoapps.contentstore.tasks", level="INFO") as logs:
            sync_discussion_settings(self.course.id, self.user)

            migration_log = f"New structure is enabled, also updating {self.course.id} to use new provider"
            assert not any(migration_log in log for log in logs.output)

    def test_handling_exceptions(self):
        """Test that exceptions are caught and logged properly."""
        test_error_message = "Test error"

        with mock.patch.object(DiscussionsConfiguration.objects, "get", side_effect=Exception(test_error_message)):
            with self.assertLogs("cms.djangoapps.contentstore.tasks", level="INFO") as logs:
                sync_discussion_settings(self.course.id, self.user)

                expected_log = (
                    f"Course import {self.course.id}: DiscussionsConfiguration sync failed: {test_error_message}"
                )
                assert any(expected_log in log for log in logs.output)
