"""Index published Pathways in the public course-discovery index."""

from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CatalogPathway
from openedx_learning import api as learning_api
from search.search_engine_base import SearchEngine

from cms.djangoapps.contentstore.courseware_index import CourseAboutSearchIndexer
from xmodule.course_block import CATALOG_VISIBILITY_CATALOG_AND_ABOUT


class PathwaySearchIndexer:
    """Create or remove one public pathway document in the shared course-info index."""

    @classmethod
    def reindex_catalog_pathway(cls, catalog_pathway_id: int | None, catalog_pathway_key: str | None = None) -> None:
        """Index a published pathway, or remove its document if it is no longer publicly defined."""
        if not CourseAboutSearchIndexer.indexing_is_enabled():
            return

        searcher = SearchEngine.get_search_engine(CourseAboutSearchIndexer.INDEX_NAME)
        if searcher is None:
            return

        if catalog_pathway_id is None:
            if catalog_pathway_key:
                searcher.remove([catalog_pathway_key])
            return

        try:
            catalog_pathway = catalog_api.get_catalog_pathway(pk=catalog_pathway_id)
        except CatalogPathway.DoesNotExist:
            if catalog_pathway_key:
                searcher.remove([catalog_pathway_key])
            return

        pathway = learning_api.get_pathway_for_catalog_pathway(catalog_pathway)
        if pathway is None or pathway.versioning.published is None:
            searcher.remove([catalog_pathway.key_str])
            return

        pathway_document = {
            "id": catalog_pathway.key_str,
            # Read back as the result's `_type`, so the catalog can tell pathways from courses in one result list.
            "document_type": "pathway",
            "org": catalog_pathway.org_code,
            "category": catalog_pathway.category.category_code,
            "course_count": len(learning_api.get_items_in_pathway(pathway, published=True)),
            "content": {
                "display_name": catalog_pathway.title,
                "description": catalog_pathway.description,
            },
            # A pathway has no start date of its own, but course discovery filters on enrollment_start, so its
            # creation date stands in for one. Catalog filtering is by the category facet, not by this.
            "enrollment_start": catalog_pathway.created,
            "catalog_visibility": CATALOG_VISIBILITY_CATALOG_AND_ABOUT,
        }
        searcher.index([pathway_document])
