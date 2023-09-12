# IBM Confidential
# OCO Source Materials
# 5737-B37, 5737-C49, 5737-H76
# (C) Copyright IBM Corp. 2021, 2022  All Rights Reserved.
# The source code for this program is not published or
# otherwise divested of its trade secrets, irrespective of
# what has been deposited with the U.S. Copyright Office.

import sys

from .client import WatsonPipelines
from .cpd_paths import CpdScope, CpdPath, CpdScopeFile
from .version import __version__

if sys.version_info[0] == 2:
    import logging

    logger = logging.getLogger('ibm_watson_pipelines_initialization')
    logger.warning("Python 2 is not supported.")
