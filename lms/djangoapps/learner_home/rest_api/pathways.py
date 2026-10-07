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
    course_keys_needing_grade_lookup = set()

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
            item_course_keys.append((str(item_entry.pathway_item.id), fulfilling_keys))

        category = catalog_pathway.category
        completed_item_ids = _get_enrollment_completed_item_ids(enrollment)
        if completed_item_ids is None:
            course_keys_needing_grade_lookup.update(
                course_key for _item_id, course_keys in item_course_keys for course_key in course_keys
            )
        pathway_sources.append((catalog_pathway, category, item_course_keys, completed_item_ids))

    # A learner can retain a passing grade after unenrolling from an individual course, so intentionally include
    # inactive CourseEnrollment rows when calculating fallback progress. Once Core exposes the provisional
    # ``PathwayEnrollment.step_completions`` relation, that enrollment-owned state takes precedence.
    passing_course_keys = set()
    if course_keys_needing_grade_lookup:
        course_enrollments = CourseEnrollment.objects.filter(
            user=user,
            course_id__in=course_keys_needing_grade_lookup,
        ).select_related("course")
        passing_course_keys = {
            str(course_enrollment.course_id)
            for course_enrollment in course_enrollments
            if user_has_passing_grade_in_course(course_enrollment)
        }

    records = []
    for catalog_pathway, category, item_course_keys, completed_item_ids in pathway_sources:
        category_label = str(category.localized_name)
        if completed_item_ids is None:
            completed_course_count = sum(
                bool(course_keys.intersection(passing_course_keys)) for _item_id, course_keys in item_course_keys
            )
        else:
            completed_course_count = sum(item_id in completed_item_ids for item_id, _course_keys in item_course_keys)

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
                "completedCourseCount": completed_course_count,
            },
            "provider": {"name": str(catalog_pathway.org.name)},
        }
        records.append(
            {
                "data": pathway_data,
                "course_ids": {course_key for _item_id, course_keys in item_course_keys for course_key in course_keys},
                "category_code": category.category_code,
                "category_label": category_label,
            }
        )

    return records


def _get_enrollment_completed_item_ids(enrollment):
    """Return completed Pathway Item IDs recorded for this enrollment, when supported by Core.

    Provisional Core contract for discussion: ``PathwayEnrollment.step_completions`` is a related manager whose rows
    expose ``pathway_item_id`` and ``is_complete``. The pinned Core PR #820 doesn't include this relation yet, so return
    ``None`` and preserve grade-derived progress until that model/API lands.
    """
    step_completions = getattr(enrollment, "step_completions", None)
    if step_completions is None:
        return None
    return {
        str(pathway_item_id)
        for pathway_item_id in step_completions.filter(is_complete=True).values_list("pathway_item_id", flat=True)
    }


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
