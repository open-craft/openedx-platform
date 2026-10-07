"""Rebuild public search documents for all Catalog Pathways."""

from django.core.management import BaseCommand
from openedx_catalog import api as catalog_api

from cms.djangoapps.contentstore.pathway_search import PathwaySearchIndexer


class Command(BaseCommand):
    """Reindex published Pathways in the shared course-info search index."""

    help = "Reindex all Catalog Pathways for public course discovery."

    def handle(self, *args, **options):  # pylint: disable=unused-argument
        pathways = catalog_api.get_catalog_pathways()
        total = pathways.count()
        self.stdout.write(f"Reindexing {total} Catalog Pathways...")

        for catalog_pathway in pathways:
            PathwaySearchIndexer.reindex_catalog_pathway(catalog_pathway.id, catalog_pathway.key_str)

        self.stdout.write(self.style.SUCCESS(f"Finished reindexing {total} Catalog Pathways."))
