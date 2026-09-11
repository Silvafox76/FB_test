"""`python -m monitor.registry` seeds the registry. Run by `make up` after migrations."""

import sys

from monitor.registry.load import main

sys.exit(main())
