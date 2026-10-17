"""Domain errors shared by core rules, storage backends and routes."""


class RevisionConflict(ValueError):
    """A save was based on an outdated revision or targets another project's document."""
