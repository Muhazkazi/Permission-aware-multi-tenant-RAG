"""Run inside the disposable Linux image via verify-image.ps1."""

import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic.config import Config
from alembic.script import ScriptDirectory
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.main import app
from app.services import pdf_worker
from app.services.extraction import extract_text
from app.services.private_storage import tenant_directory

assert "/documents/upload" in app.openapi()["paths"]
assert "site-packages" in Path(pdf_worker.__file__).parts
os.chdir("/app")
assert (
    ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    == "20261125_0003"
)
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)

writer = PdfWriter()
page = writer.add_blank_page(200, 200)
stream = DecodedStreamObject()
stream.set_data(b"BT /F1 12 Tf (smoke check) Tj ET")
font = DictionaryObject(
    {
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }
)
page[NameObject("/Resources")] = DictionaryObject(
    {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
)
page[NameObject("/Contents")] = writer._add_object(stream)
output = BytesIO()
writer.write(output)
assert extract_text(output.getvalue(), "application/pdf").strip() == "smoke check"

with TemporaryDirectory() as tmp:
    root = Path(tmp) / "private"
    with tenant_directory(root, "a" * 64) as fd:
        assert os.fstat(fd).st_mode & 0o777 == 0o700
    # A link at a generated tenant component must fail closed.
    (root / ("b" * 64)).symlink_to(Path(tmp), target_is_directory=True)
    try:
        with tenant_directory(root, "b" * 64):
            raise AssertionError("Accepted tenant symlink")
    except OSError:
        pass
    if os.geteuid() == 0:
        os.chown(root, 65534, -1)
        try:
            with tenant_directory(root, "a" * 64):
                raise AssertionError("Accepted foreign owner")
        except ValueError:
            pass
        os.chown(root, 0, -1)
    root.chmod(0o755)
    try:
        with tenant_directory(root, "a" * 64):
            raise AssertionError("Accepted public root")
    except ValueError:
        pass
print("Verified installed wheel, upload route, migration, PDF extraction and storage")
