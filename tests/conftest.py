import os
import tempfile

# Must be set before app.config is imported.
os.environ["TUF_UPDATE_DATA_DIR"] = tempfile.mkdtemp(prefix="tuf-test-data-")
