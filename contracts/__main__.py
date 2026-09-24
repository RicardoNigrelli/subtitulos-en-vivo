"""`python -m contracts <archivos>` es lo mismo que `python -m contracts.validate <archivos>`."""
import sys

from contracts.validate import main

sys.exit(main())
