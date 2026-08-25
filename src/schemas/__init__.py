from src.schemas import banks  # noqa: F401 — dispara @register dos handlers
from src.schemas import excel  # noqa: F401 — dispara @register dos handlers Excel
from src.schemas import fecfin  # noqa: F401 — dispara @register dos handlers FECFIN
from src.schemas.registry import LayoutNotRecognized, dispatch  # noqa: F401
from src.schemas.excel.registry import ExcelLayoutNotRecognized, dispatch_excel  # noqa: F401
from src.schemas.fecfin.registry import FecfinLayoutNotRecognized, dispatch_fecwin  # noqa: F401
