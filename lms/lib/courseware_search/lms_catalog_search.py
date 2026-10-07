"""
Course discovery search for the public catalog, which mixes courses and Pathways in one result list.

edx-search serves this endpoint itself; all this adds is the learner-facing name of each Pathway category, which is
translatable and so can't live in the search index.
"""
import json

from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import PathwayCategory
from search.views import course_list_search as edx_search_course_list_search

PATHWAY_DOCUMENT_TYPE = "pathway"


def course_list_search(request):
    """Run the standard discovery search, then label Pathway categories with their localized names."""
    response = edx_search_course_list_search(request)
    if response.status_code != 200:
        return response

    data = json.loads(response.content)
    category_aggregation = data.get("aggs", {}).get("category")

    category_labels = {}
    for category_code in _referenced_category_codes(data):
        try:
            category = catalog_api.get_pathway_category(category_code)
        except PathwayCategory.DoesNotExist:
            continue
        category_labels[category_code] = category.localized_name

    if category_aggregation and category_labels:
        category_aggregation["labels"] = category_labels

    for result in data.get("results", []):
        if result.get("_type") != PATHWAY_DOCUMENT_TYPE:
            continue
        category_code = result.get("data", {}).get("category")
        if category_code in category_labels:
            result["data"]["category_label"] = category_labels[category_code]

    response.content = json.dumps(data)
    return response


def _referenced_category_codes(data):
    """Get every category code the response refers to: in the facet and in the Pathways themselves."""
    codes = set(data.get("aggs", {}).get("category", {}).get("terms", {}))
    for result in data.get("results", []):
        if result.get("_type") != PATHWAY_DOCUMENT_TYPE:
            continue
        category_code = result.get("data", {}).get("category")
        if category_code:
            codes.add(category_code)
    return codes
