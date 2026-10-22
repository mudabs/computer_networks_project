"""Static routes used by tests, independent of the editable data/routes.json."""
import copy

UPPER_ROUTES = {
    "A": {"H1": "H1", "H2": "B"},
    "B": {"H1": "A", "H2": "D"},
    "C": {"H1": "A", "H2": "D"},
    "D": {"H2": "H2", "H1": "B"},
}


def use_upper_path(config):
    """Replace the config's loaded routes with the upper path H1-A-B-D-H2."""
    config.route_data = copy.deepcopy(UPPER_ROUTES)
    return config
