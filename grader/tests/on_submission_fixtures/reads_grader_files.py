# Isolated mode and a minimal environment: no PYTHONPATH tricks, no grader imports.
import os
import sys
coins = int(input("How many coins? "))
print("You have " + str(coins * 10) + " points.")
print("ENV=" + ",".join(sorted(k for k in os.environ if k not in ("PYTHONIOENCODING", "PYTHONHASHSEED", "LC_CTYPE"))))
print("ISOLATED=" + str(sys.flags.isolated))
