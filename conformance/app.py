"""Railway/local entry point for the Workspace Core conformance consumer."""

from .application import create_conformance_runtime

runtime = create_conformance_runtime()
app = runtime.app

if __name__ == "__main__":
    app.run()
