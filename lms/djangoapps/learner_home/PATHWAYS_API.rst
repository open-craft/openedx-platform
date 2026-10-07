Learner Pathways REST API
=========================

The learner-home API exposes authenticated, read-only Pathway data for the
Learner Home MFE. The catalog/content split follows
`openedx-core ADR 0007 <https://github.com/openedx/openedx-core/blob/main/docs/openedx_learning/decisions/0007-pathway-catalog-content-split.rst>`_.

List the current learner's active, published pathways grouped by category::

  GET /api/learner_home/v1/pathways/

Find those pathways for one or more course runs::

  GET /api/learner_home/v1/pathways/by_course/?course_ids=course-v1%3Aorg%2Bcourse%2Brun&course_ids=...

``course_ids`` is a repeatable query parameter containing serialized Open edX
CourseKeys. The response is a mapping from each requested CourseKey to the
learner's enrolled pathways that contain it. Requested courses without matching
pathways map to an empty list. An omitted ``course_ids`` parameter returns an
empty mapping.

Both endpoints use the authenticated learner by default. Staff may pass the
learner-home ``user`` query parameter to use the existing learner masquerade
behavior. Results include active Pathway enrollments only, and omit pathways
without a published content definition.

Pathway progress counts a published Pathway Item complete when the learner has
a passing grade in any CourseRun that fulfills that Item. ``courseCount`` is
the number of published Items, and ``completedCourseCount`` is the number of
completed Items.

.. note::

   The current openedx-core category API exposes a localized singular name,
   not a plural label. Until a plural field is agreed upstream, the
   ``categoryLabelPlural`` response field contains that localized singular
   name. Category colors are not currently provided.
