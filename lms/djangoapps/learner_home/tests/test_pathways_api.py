"""Tests for learner-facing Pathway REST APIs."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import resolve
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator
from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CatalogCourse, CourseRun
from openedx_content.applets.publishing import api as publishing_api
from openedx_learning import api as learning_api
from organizations.api import ensure_organization
from rest_framework.test import APIRequestFactory, force_authenticate

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.learner_home.rest_api.pathways import (
    LearnerPathwaysByCourseView,
    LearnerPathwaysView,
    _get_learner_pathway_records,
    _group_pathways_by_category,
)


class LearnerPathwaysApiTest(TestCase):
    """Tests for pathway listing and course-to-pathway mapping."""

    def setUp(self):
        self.user = UserFactory()
        self.request_factory = APIRequestFactory()

    def test_rest_api_urls_register_pathway_endpoints(self):
        urlconf = "lms.djangoapps.learner_home.rest_api.urls"
        list_match = resolve("/v1/pathways/", urlconf=urlconf)
        by_course_match = resolve("/v1/pathways/by_course/", urlconf=urlconf)

        assert list_match.func.view_class is LearnerPathwaysView
        assert by_course_match.func.view_class is LearnerPathwaysByCourseView

    def test_list_groups_enrollments_by_category(self):
        def pathway_data(identifier):
            return {
                "pathway": {
                    "id": identifier,
                    "content": {"displayName": identifier},
                    "courseCount": 1,
                    "category": "certificate",
                    "categoryLabel": "Certificate",
                },
                "progress": {"completedCourseCount": 0},
                "provider": {"name": "Open edX"},
            }

        records = [
            {"category_code": "certificate", "category_label": "Certificate", "data": pathway_data("one")},
            {"category_code": "certificate", "category_label": "Certificate", "data": pathway_data("two")},
            {"category_code": "bootcamp", "category_label": "Bootcamp", "data": pathway_data("three")},
        ]
        request = self.request_factory.get("/api/learner_home/v1/pathways/")
        force_authenticate(request, user=self.user)

        with patch(
            "lms.djangoapps.learner_home.rest_api.pathways._get_learner_pathway_records",
            return_value=records,
        ):
            response = LearnerPathwaysView.as_view()(request)

        assert response.status_code == 200
        assert response.data == [
            {
                "categoryLabelPlural": "Certificate",
                "pathways": [pathway_data("one"), pathway_data("two")],
            },
            {"categoryLabelPlural": "Bootcamp", "pathways": [pathway_data("three")]},
        ]

    def test_pathways_by_course_returns_empty_values_for_unmatched_courses(self):
        course_with_pathway = "course-v1:openedx+test+2026"
        course_without_pathway = "course-v1:openedx+other+2026"
        pathway_data = {
            "pathway": {
                "id": "catalog-pathway:openedx:program",
                "content": {"displayName": "My Program"},
                "courseCount": 1,
                "category": "certificate",
                "categoryLabel": "Certificate",
            },
            "progress": {"completedCourseCount": 0},
            "provider": {"name": "Open edX"},
        }
        records = [{"course_ids": {course_with_pathway}, "data": pathway_data}]
        request = self.request_factory.get(
            "/api/learner_home/v1/pathways/by_course/",
            {"course_ids": [course_with_pathway, course_without_pathway]},
        )
        force_authenticate(request, user=self.user)

        with patch(
            "lms.djangoapps.learner_home.rest_api.pathways._get_learner_pathway_records",
            return_value=records,
        ):
            response = LearnerPathwaysByCourseView.as_view()(request)

        assert response.status_code == 200
        assert response.data == {
            course_with_pathway: [pathway_data],
            course_without_pathway: [],
        }

    def test_pathways_by_course_rejects_invalid_course_keys(self):
        request = self.request_factory.get(
            "/api/learner_home/v1/pathways/by_course/",
            {"course_ids": "not-a-course-key"},
        )
        force_authenticate(request, user=self.user)

        response = LearnerPathwaysByCourseView.as_view()(request)

        assert response.status_code == 400
        assert "course_ids" in response.data

    def test_pathways_by_course_without_course_ids_returns_empty_map(self):
        request = self.request_factory.get("/api/learner_home/v1/pathways/by_course/")
        force_authenticate(request, user=self.user)

        with patch("lms.djangoapps.learner_home.rest_api.pathways._get_learner_pathway_records") as get_records:
            response = LearnerPathwaysByCourseView.as_view()(request)

        assert response.status_code == 200
        assert response.data == {}
        get_records.assert_not_called()

    def test_record_payload_uses_published_items_and_passing_course_enrollments(self):
        category = SimpleNamespace(category_code="certificate", localized_name="Certificate")
        catalog_pathway = SimpleNamespace(
            category=category,
            key_str="catalog-pathway:openedx:program",
            title="My Program",
            org=SimpleNamespace(name="Open edX"),
        )
        pathway = SimpleNamespace(versioning=SimpleNamespace(published=SimpleNamespace(title="Authoring title")))
        first_item = SimpleNamespace(pathway_item="item-1")
        second_item = SimpleNamespace(pathway_item="item-2")
        course_one = CourseKey.from_string("course-v1:openedx+one+2026")
        course_two = CourseKey.from_string("course-v1:openedx+two+2026")
        course_three = CourseKey.from_string("course-v1:openedx+three+2026")
        run_entry = lambda course_key: SimpleNamespace(course_run=SimpleNamespace(course_key=course_key))
        grade_enrollments = [
            SimpleNamespace(course_id=course_one),
            SimpleNamespace(course_id=course_two),
            SimpleNamespace(course_id=course_three),
        ]

        with (
            patch.object(
                catalog_api,
                "get_pathway_enrollments",
                create=True,
                return_value=[
                    SimpleNamespace(catalog_pathway=catalog_pathway),
                ],
            ),
            patch.object(learning_api, "get_pathway_for_catalog_pathway", create=True, return_value=pathway),
            patch.object(
                learning_api,
                "get_items_in_pathway",
                create=True,
                return_value=[first_item, second_item],
            ),
            patch.object(
                learning_api,
                "get_course_runs_for_item",
                create=True,
                side_effect=[[run_entry(course_one), run_entry(course_two)], [run_entry(course_three)]],
            ),
            patch(
                "lms.djangoapps.learner_home.rest_api.pathways.CourseEnrollment.objects.filter",
                return_value=SimpleNamespace(select_related=lambda _field: grade_enrollments),
            ),
            patch(
                "lms.djangoapps.learner_home.rest_api.pathways.user_has_passing_grade_in_course",
                side_effect=[False, True, True],
            ),
        ):
            records = _get_learner_pathway_records(self.user)

        assert len(records) == 1
        assert records[0]["data"]["pathway"]["content"]["displayName"] == "My Program"
        assert records[0]["data"]["pathway"]["courseCount"] == 2
        assert records[0]["data"]["progress"]["completedCourseCount"] == 2
        assert records[0]["course_ids"] == {str(course_one), str(course_two), str(course_three)}

    def test_record_payload_uses_core_catalog_and_published_pathway_apis(self):
        ensure_organization("PathwaysApiTest")
        catalog_pathway = catalog_api.create_catalog_pathway(
            org_code="PathwaysApiTest",
            pathway_code="DataScience",
            title="Data Science Certificate",
        )
        created = datetime(2026, 10, 7, tzinfo=UTC)
        pathway, _version = learning_api.create_pathway_and_version(
            catalog_pathway=catalog_pathway,
            title="Authoring title",
            created=created,
        )
        catalog_course = CatalogCourse.objects.create(
            org_code="PathwaysApiTest",
            course_code="DataScience",
        )
        course_run = CourseRun.objects.create(
            catalog_course=catalog_course,
            run_code="2026",
            course_key=CourseLocator(org="PathwaysApiTest", course="DataScience", run="2026"),
        )
        item, _item_version = learning_api.create_pathway_item_and_version(
            pathway,
            "first-item",
            title="First item",
            course_runs=[course_run],
            created=created,
        )
        learning_api.create_next_pathway_version(pathway, items=[item], created=created)
        publishing_api.publish_all_drafts(pathway.learning_package_id, published_at=created)
        catalog_api.enroll_in_pathway(self.user.id, catalog_pathway)
        CourseEnrollmentFactory(user=self.user, course_id=course_run.course_key)

        with patch(
            "lms.djangoapps.learner_home.rest_api.pathways.user_has_passing_grade_in_course",
            return_value=True,
        ):
            records = _get_learner_pathway_records(self.user)

        assert len(records) == 1
        assert records[0]["data"]["pathway"]["id"] == catalog_pathway.key_str
        assert records[0]["data"]["pathway"]["content"]["displayName"] == "Data Science Certificate"
        assert records[0]["data"]["pathway"]["courseCount"] == 1
        assert records[0]["data"]["progress"]["completedCourseCount"] == 1
        assert records[0]["course_ids"] == {str(course_run.course_key)}
        assert _group_pathways_by_category(records)[0]["categoryLabelPlural"] == "Pathway"
