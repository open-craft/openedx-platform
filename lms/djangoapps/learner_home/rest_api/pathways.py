"""Learner-facing REST APIs for Pathways."""

from collections import OrderedDict

from django.utils.translation import gettext as _
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun
from openedx_learning import api as learning_api
from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from lms.djangoapps.learner_home.utils import get_masquerade_user
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser


class PathwayContentSerializer(serializers.Serializer):
    """The learner-facing display name expected by the Learner Home MFE."""

    displayName = serializers.CharField()


class PathwaySerializer(serializers.Serializer):
    """Catalog and published content fields for one Pathway."""

    id = serializers.CharField()
    content = PathwayContentSerializer()
    courseCount = serializers.IntegerField()
    category = serializers.CharField()
    categoryLabel = serializers.CharField()


class PathwayProgressSerializer(serializers.Serializer):
    completedCourseCount = serializers.IntegerField()


class PathwayProviderSerializer(serializers.Serializer):
    name = serializers.CharField()


class LearnerPathwaySerializer(serializers.Serializer):
    pathway = PathwaySerializer()
    progress = PathwayProgressSerializer()
    provider = PathwayProviderSerializer(required=False, allow_null=True)


class PathwayCategoryGroupSerializer(serializers.Serializer):
    categoryLabelPlural = serializers.CharField()
    pathways = LearnerPathwaySerializer(many=True)


class PathwaysByCourseSerializer(serializers.BaseSerializer):
    """Mapping from serialized CourseKeys to pathways containing the course run."""

    def to_representation(self, instance):
        # Keep CourseKeys as dynamic object keys while validating each pathway against the shared response schema.
        return {
            course_id: LearnerPathwaySerializer(pathways, many=True).data for course_id, pathways in instance.items()
        }


def _get_learner_pathway_records(user):
    """Return display-ready data for the learner's active Pathway enrollments.

    Enrollment-owned item counts provide progress. The Content API is used only to check that the catalog pathway has a
    published definition; course-to-pathway membership is resolved separately through Content's published definitions.

    This design assumes the planned ``PathwayEnrollment.total_item_count`` and
    ``PathwayEnrollment.completed_item_count`` fields, which are not in the current openedx-core#820 revision.
    """
    records = []

    for enrollment in catalog_api.get_pathway_enrollments(user.id):
        catalog_pathway = enrollment.catalog_pathway
        pathway = learning_api.get_pathway_for_catalog_pathway(catalog_pathway)
        if pathway is None:
            continue

        published_version = pathway.versioning.published
        if published_version is None:
            continue

        category = catalog_pathway.category
        category_label = str(category.localized_name)
        pathway_data = {
            "pathway": {
                # Never expose the internal database primary key. key_str is the public catalog identifier currently
                # provided by openedx-core; replace it if that API adopts an OpaqueKey representation.
                "id": catalog_pathway.key_str,
                "content": {"displayName": str(catalog_pathway.title)},
                "courseCount": enrollment.total_item_count,
                "category": category.category_code,
                "categoryLabel": category_label,
            },
            "progress": {
                "completedCourseCount": enrollment.completed_item_count,
            },
            "provider": {"name": str(catalog_pathway.org.name)},
        }
        records.append(
            {
                "data": pathway_data,
                "catalog_pathway_id": catalog_pathway.id,
                "category_code": category.category_code,
                "category_label": category_label,
            }
        )

    return records


def _group_pathways_by_category(records):
    """Serialize a learner's enrolled pathways grouped in enrollment order by category."""
    categories = OrderedDict()
    for record in records:
        category_code = record["category_code"]
        if category_code not in categories:
            # openedx-core currently models/translates only the singular category name. Keep the frontend's plural
            # field populated with that localized title until the Core API provides an explicit plural form.
            categories[category_code] = {
                "categoryLabelPlural": record["category_label"],
                "pathways": [],
            }
        categories[category_code]["pathways"].append(record["data"])
    return list(categories.values())


def _get_requested_course_ids(request):
    """Parse the repeated ``course_ids`` query parameter as canonical CourseKeys."""
    course_ids = request.query_params.getlist("course_ids")
    try:
        return list(dict.fromkeys(str(CourseKey.from_string(course_id)) for course_id in course_ids))
    except InvalidKeyError as error:
        raise ValidationError({"course_ids": _("Each course_ids value must be a valid course key.")}) from error


def _pathways_by_course(records, course_ids):
    """Return enrolled pathways containing each requested CourseRun via the published Content definitions."""
    enrolled_pathways = {record["catalog_pathway_id"]: record["data"] for record in records}
    pathways_by_course = {}

    for course_id in course_ids:
        try:
            course_run = catalog_api.get_course_run(CourseKey.from_string(course_id))
        except CourseRun.DoesNotExist:
            pathways_by_course[course_id] = []
            continue

        pathway_ids = {
            pathway.catalog_pathway_id
            for pathway in learning_api.get_pathways_containing_course_run(course_run, published=True)
        }
        pathways_by_course[course_id] = [
            pathway_data
            for catalog_pathway_id, pathway_data in enrolled_pathways.items()
            if catalog_pathway_id in pathway_ids
        ]

    return pathways_by_course


class LearnerPathwaysView(generics.GenericAPIView):
    """List the authenticated learner's active pathways, grouped by category.

    GET ``/api/learner_home/v1/pathways/``

    Staff may pass ``user=<username-or-email>`` to use learner-home's existing masquerade behavior.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, NotJwtRestrictedApplication)
    serializer_class = PathwayCategoryGroupSerializer

    def get(self, request):
        user = get_masquerade_user(request) or request.user
        data = _group_pathways_by_category(_get_learner_pathway_records(user))
        return Response(self.get_serializer(data, many=True).data)


class LearnerPathwaysByCourseView(generics.GenericAPIView):
    """List enrolled pathways that contain each requested course run.

    GET ``/api/learner_home/v1/pathways/by_course/?course_ids=<course-key>``

    ``course_ids`` may be repeated. Staff may pass ``user=<username-or-email>`` to use learner-home's existing
    masquerade behavior.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, NotJwtRestrictedApplication)
    serializer_class = PathwaysByCourseSerializer

    def get(self, request):
        user = get_masquerade_user(request) or request.user
        course_ids = _get_requested_course_ids(request)
        if not course_ids:
            return Response(self.get_serializer({}).data)
        records = _get_learner_pathway_records(user)
        return Response(self.get_serializer(_pathways_by_course(records, course_ids)).data)
