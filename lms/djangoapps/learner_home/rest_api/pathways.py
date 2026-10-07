"""Learner-facing REST APIs for Pathways."""

from collections import OrderedDict

from django.utils.translation import gettext as _
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx_catalog import api as catalog_api
from openedx_learning import api as learning_api
from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.student.helpers import user_has_passing_grade_in_course
from common.djangoapps.student.models.course_enrollment import CourseEnrollment
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
    """Return display-ready pathway data and each pathway's fulfilling course IDs.

    Only active catalog enrollments with a currently published content definition are included.
    The Pathway content/catalog split is intentional: catalog metadata is used for the learner-facing title, category,
    and provider, while the published content definition supplies the ordered Items and fulfillment CourseRuns.
    """
    pathway_sources = []
    course_keys = set()

    for enrollment in catalog_api.get_pathway_enrollments(user.id):
        catalog_pathway = enrollment.catalog_pathway
        pathway = learning_api.get_pathway_for_catalog_pathway(catalog_pathway)
        if pathway is None:
            continue

        published_version = pathway.versioning.published
        if published_version is None:
            continue

        item_course_keys = []
        for item_entry in learning_api.get_items_in_pathway(pathway, published=True):
            fulfilling_keys = {
                str(run_entry.course_run.course_key)
                for run_entry in learning_api.get_course_runs_for_item(item_entry.pathway_item, published=True)
            }
            item_course_keys.append(fulfilling_keys)
            course_keys.update(fulfilling_keys)

        category = catalog_pathway.category
        pathway_sources.append((catalog_pathway, category, item_course_keys))

    # A learner can retain a passing grade after unenrolling from an individual course, so intentionally include
    # inactive CourseEnrollment rows when calculating pathway progress.
    passing_course_keys = set()
    if course_keys:
        course_enrollments = CourseEnrollment.objects.filter(
            user=user,
            course_id__in=course_keys,
        ).select_related("course")
        passing_course_keys = {
            str(course_enrollment.course_id)
            for course_enrollment in course_enrollments
            if user_has_passing_grade_in_course(course_enrollment)
        }

    records = []
    for catalog_pathway, category, item_course_keys in pathway_sources:
        category_label = str(category.localized_name)
        pathway_data = {
            "pathway": {
                # Never expose the internal database primary key. key_str is the public catalog identifier currently
                # provided by openedx-core; replace it if that API adopts an OpaqueKey representation.
                "id": catalog_pathway.key_str,
                "content": {"displayName": str(catalog_pathway.title)},
                "courseCount": len(item_course_keys),
                "category": category.category_code,
                "categoryLabel": category_label,
            },
            "progress": {
                "completedCourseCount": sum(
                    bool(item_keys.intersection(passing_course_keys)) for item_keys in item_course_keys
                ),
            },
            "provider": {"name": str(catalog_pathway.org.name)},
        }
        records.append(
            {
                "data": pathway_data,
                "course_ids": set.union(*item_course_keys) if item_course_keys else set(),
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
    """Return pathway data keyed by every requested course ID (including courses with no pathways)."""
    return {
        course_id: [record["data"] for record in records if course_id in record["course_ids"]]
        for course_id in course_ids
    }


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
