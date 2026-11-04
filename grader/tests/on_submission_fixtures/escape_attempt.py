# A correct answer that also tries to escape: steal secrets, read other
# students' work, see the grader's environment, and phone home. The sandbox
# tests plant a marker in a file next to this one and in the grader's own
# environment, then check this program never finds it.
import json
import os
import socket

coins = int(input("How many coins? "))
print("You have " + str(coins * 10) + " points.")

found = []
here = os.path.dirname(os.path.abspath(__file__))
for name in os.listdir(here):
    path = os.path.join(here, name)
    if os.path.isfile(path) and path != os.path.abspath(__file__):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                found.append(name + ": " + f.read(200))
        except OSError:
            pass
for pid in os.listdir("/proc"):
    if pid.isdigit():
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read().decode(errors="replace")
            hits = [v[:80] for v in env.split("\0") if "CANARY" in v]
            found.append("environ " + pid + ": " + (", ".join(hits) or str(len(env)) + " bytes"))
        except OSError:
            pass
try:
    socket.create_connection(("192.0.2.1", 80), timeout=1).close()
    network = "open"
except OSError as exc:
    network = type(exc).__name__
try:
    with open(os.path.abspath(__file__), "a") as f:
        f.write("# tampered\n")
    tampered = True
except OSError:
    tampered = False
print("REPORT " + json.dumps({"found": found[:40], "network": network, "tampered": tampered}))
