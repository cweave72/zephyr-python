import importlib
import yaml
import logging
from rich import inspect

logger = logging.getLogger(__name__)

def load_callset_yaml(callset_yaml: str):
    """Loads callset.yaml file.
    """
    with open(callset_yaml, 'r') as stream:
        d = yaml.safe_load(stream)
    return d


def import_class(pkg_name: str, mod_name: str, cls_name: str):
    """Imports class from module.
    Returns the imported class object.
    """
    try:
        logger.debug(f"performing: from {pkg_name}.{mod_name} import {cls_name}")
        mod = importlib.import_module(f"{pkg_name}.{mod_name}")
        cls = getattr(mod, cls_name)
        return cls
    except (ImportError, AttributeError) as e:
        msg = f"Error importing class {cls_name} from {pkg_name}.{mod_name}: {str(e)}"
        logger.error(msg)
        raise e


def get_callsets(callset_dict: dict):
    """Import callsets and return a list of tuples
    [(callset_cls, callset_id), ...]
    Input callset_dict format:
        { id:
            {  pkg: <name>,
               mod: <name>,
               cls: <name>,
            },
          ...
        }
    """
    callsets = []
    for _id in callset_dict:
        pkg_name = callset_dict[_id]['pkg']
        mod_name = callset_dict[_id]['mod']
        cls_name = callset_dict[_id]['cls']
        cls = import_class(pkg_name, mod_name, cls_name)
        logger.debug(f"Callset id: {_id} -> {pkg_name}.{mod_name}.{cls_name}")
        callsets.append((cls, _id))

    return callsets
