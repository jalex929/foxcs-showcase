"""A tiny module for testing the mutation tester itself."""

LIMIT = 10  # module-level setting: never mutated


def grade(points, possible):
    """Docstrings are never mutated."""
    if possible <= 0 or points < 0:
        return None
    if points >= possible:
        return "full"
    return "partial" if points > 0 else "none"


def is_passing(points):
    return not points < 5
