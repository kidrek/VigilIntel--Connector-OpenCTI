"""VigilIntel STIX Importer - OpenCTI External Import Connector."""

import sys
import os
import traceback

from lib.vigilintel import VigilIntelConnector

if __name__ == "__main__":
    try:
        connector = VigilIntelConnector()
        connector.run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
